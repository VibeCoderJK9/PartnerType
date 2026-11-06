"""
PartnerType -- reopening a mistakenly won/lost lead.

The shape being pinned: only a producer admin may pull a closed lead back
into an editable, active, nonterminal stage -- and only after the money is
accounted for. A recorded commission with no settlement review blocks an
ordinary reopen ('unknown'); an explicit producer review that finds it
unpaid ('unpaid') unblocks it; an explicit review that finds it already paid
('paid') is a dead end for this plan (a correction is a separate,
not-yet-built workflow). A lead with no recorded commission at all -- most
lost leads, most wins the auto-commission setting never stamped -- reopens
directly, no review required.

Everything here runs through the real router: the
guarantees under test are about who may call what, under what version, and
what an already-closed door refuses -- a domain-level call cannot see the
routing and authorization standing in front of it.
"""
from api.tests.context import carry_context
import bcrypt
import csv
import io
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest

from api import db as db_module
from api import lead_fields
from api.db import get_conn


# ─── HELPERS ────────────────────────────────────────────────────────────────

def test_legacy_lost_with_win_date_regains_partner_editor_after_review(
    call_api, sample_lead, admin_user, partner_user, db_conn,
):
    admin_token = _login(call_api, admin_user)
    partner_token = _login(call_api, partner_user)
    lead_id = sample_lead["id"]
    _lose(call_api, lead_id, admin_token, partner_token)
    db_conn.execute("UPDATE leads SET won_at=?, settlement_status='unknown' WHERE id=?",
                    ("2025-01-01T12:00:00", lead_id))
    db_conn.commit()
    status, body = _review(call_api, admin_token, lead_id, result="unpaid", reason="Historyczny zapis sprawdzony")
    assert status == 200, body
    status, body = _reopen(call_api, admin_token, lead_id, target_stage="contact", reason="Korekta historii")
    assert status == 200, body
    status, detail = call_api("GET", f"/api/leads/{lead_id}", token=partner_token)
    assert status == 200, detail
    assert detail["lead"]["permissions"]["edit_details"] is True
    history = json.loads(_events(lead_id, "lead.reopen")[-1]["payload_json"])
    assert history["prior_won_at"] == "2025-01-01T12:00:00"

def test_paid_review_and_reopen_compete_on_the_same_revision(
    call_api, sample_lead, admin_user, partner_user,
):
    from api.leads import reopen_lead, review_reopen_eligibility
    from api.db import StaleRevisionError
    token = _login(call_api, admin_user)
    _win(call_api, sample_lead["id"], token, _login(call_api, partner_user))
    revision = _revision(sample_lead["id"])
    barrier = Barrier(2)

    def run(action):
        barrier.wait(timeout=5)
        try:
            if action == "paid":
                review_reopen_eligibility(sample_lead["id"], admin_user, "paid",
                    reason="Zweryfikowana wypłata", confirmed=True,
                    expected_revision=revision, operation_id="race-paid")
            else:
                reopen_lead(sample_lead["id"], admin_user, "contact", reason="Pomyłka",
                    confirmed=True, expected_revision=revision, operation_id="race-reopen")
            return action, "saved"
        except StaleRevisionError:
            return action, "stale"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = dict(pool.map(carry_context(run), ["paid", "reopen"]))
    assert sorted(results.values()) == ["saved", "stale"]
    row = _lead_row(sample_lead["id"])
    assert row["revision"] == revision + 1
    if results["paid"] == "saved":
        assert row["status"] == "won" and row["settlement_status"] == "paid"
        assert not _events(sample_lead["id"], "lead.reopen")
    else:
        assert row["status"] == "contact" and row["commission"] is None
        assert not _events(sample_lead["id"], "lead.reopen_eligibility")


def test_win_reopen_rewin_agrees_across_financial_readers(call_api, sample_lead, admin_user, partner_user):
    from api import stats, partners, export
    token = _login(call_api, admin_user)
    partner_token = _login(call_api, partner_user)
    first = _win(call_api, sample_lead["id"], token, partner_token)
    amount = first["commission"]
    assert amount is not None

    def check(expected, count):
        assert stats.get_admin_stats()["total_commissions"] == expected
        assert stats.get_partner_stats(partner_user["id"])["total_earnings"] == expected
        assert partners.get_partner(partner_user["id"])["total_earnings"] == expected
        assert next(p for p in partners.get_partners() if p["id"] == partner_user["id"])["total_earnings"] == expected
        assert stats.get_commission_payouts()["month"]["amount"] == expected
        for actor in (admin_user, partner_user):
            rows = list(csv.DictReader(io.StringIO(export._commissions_csv(actor))))
            assert len(rows) == count
            assert sum(float(r["Prowizja (PLN)"]) for r in rows) == expected

    check(amount, 1)
    _, review = _review(call_api, token, sample_lead["id"], result="unpaid", reason="Dowód kontroli")
    reviewed = review["lead"]
    status, body = _reopen(call_api, token, sample_lead["id"], target_stage="contact", reason="Pomyłka")
    assert status == 200, body
    check(0, 0)
    snapshot = json.loads(_events(sample_lead["id"], "lead.reopen")[0]["payload_json"])
    assert snapshot["prior_settlement_reviewed_by"] == admin_user["id"]
    assert snapshot["prior_settlement_reviewed_at"] == reviewed["settlement_reviewed_at"]
    assert snapshot["prior_settlement_review_reason"] == "Dowód kontroli"
    status, body = _move_stage(call_api, partner_token, sample_lead["id"], target_stage="won", confirmed_terminal=True)
    assert status == 200, body
    check(amount, 1)


@pytest.mark.parametrize("settlement", ["paid", "unknown"])
def test_missing_amount_does_not_erase_settlement_evidence(
    call_api, sample_lead, admin_user, partner_user, db_conn, settlement,
):
    token = _login(call_api, admin_user)
    _win(call_api, sample_lead["id"], token, _login(call_api, partner_user))
    db_conn.execute("UPDATE leads SET commission=NULL, settlement_status=? WHERE id=?",
                    (settlement, sample_lead["id"]))
    db_conn.commit()
    before = _lead_row(sample_lead["id"])
    status, body = _reopen(call_api, token, sample_lead["id"], target_stage="contact", reason="Sprawdzenie")
    assert status == 409, body
    assert body["code"] == "settlement_" + settlement
    assert _lead_row(sample_lead["id"]) == before


@pytest.mark.parametrize("registration", ["pending", "needs_info", "rejected"])
def test_reopen_requires_accepted_registration(call_api, sample_lead, admin_user, db_conn, registration):
    db_conn.execute("UPDATE leads SET status='lost', registration_status=? WHERE id=?",
                    (registration, sample_lead["id"]))
    db_conn.commit()
    before = _lead_row(sample_lead["id"])
    status, body = _reopen(call_api, _login(call_api, admin_user), sample_lead["id"],
                           target_stage="contact", reason="Nie wolno wprowadzić do lejka")
    assert status == 409, body
    assert _lead_row(sample_lead["id"]) == before


@pytest.mark.parametrize("writer", ["staff", "partner"])
@pytest.mark.parametrize("settlement", ["paid", "unknown"])
def test_win_preserves_prior_settlement_even_without_win_date(
    call_api, sample_lead, admin_user, partner_user, db_conn, writer, settlement,
):
    admin_token = _login(call_api, admin_user)
    _accept(call_api, sample_lead["id"], admin_token)
    db_conn.execute("UPDATE leads SET commission=0, won_at=NULL, settlement_status=? WHERE id=?",
                    (settlement, sample_lead["id"]))
    db_conn.commit()
    if writer == "staff":
        status, body = call_api("PATCH", f"/api/leads/{sample_lead['id']}", token=admin_token,
                                body={"status": "won"})
    else:
        status, body = _move_stage(call_api, _login(call_api, partner_user), sample_lead["id"],
                                   target_stage="won", confirmed_terminal=True)
    assert status == 200, body
    assert _lead_row(sample_lead["id"])["settlement_status"] == settlement


def test_review_allows_unknown_win_with_missing_amount(call_api, sample_lead, admin_user, db_conn):
    db_conn.execute("UPDATE leads SET registration_status='accepted', status='won', "
                    "commission=NULL, settlement_status='unknown' WHERE id=?", (sample_lead["id"],))
    db_conn.commit()
    token = _login(call_api, admin_user)
    status, body = _review(call_api, token, sample_lead["id"], result="unpaid", reason="Sprawdzono brak wypłaty")
    assert status == 200, body
    status, body = _reopen(call_api, token, sample_lead["id"], target_stage="contact", reason="Pomyłka")
    assert status == 200, body


def _login(call_api, user):
    status, body = call_api("POST", "/api/auth/login", body={
        "email": user["email"],
        "password": user["plain_password"],
        "role": user["role"],
    })
    assert status == 200, body
    return body["token"]


def _revision(lead_id):
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT revision FROM leads WHERE id = ?", (lead_id,)
        ).fetchone()["revision"]
    finally:
        conn.close()


def _lead_row(lead_id):
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _events(lead_id, event_type=None):
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM lead_workflow_events WHERE lead_id=? ORDER BY id",
            (lead_id,),
        ).fetchall()
    finally:
        conn.close()
    result = [dict(r) for r in rows]
    if event_type:
        result = [r for r in result if r["event_type"] == event_type]
    return result


def _accept(call_api, lead_id, admin_token):
    status, body = call_api(
        "PATCH", f"/api/leads/{lead_id}",
        body={"registration_status": "accepted"}, token=admin_token,
    )
    assert status == 200, body
    return body


def _move_stage(call_api, token, lead_id, **body):
    body.setdefault("expected_revision", _revision(lead_id))
    body.setdefault("operation_id", f"op-stage-{lead_id}-{body.get('target_stage', 'x')}-{_revision(lead_id)}")
    return call_api("PATCH", f"/api/leads/{lead_id}/stage", token=token, body=body)


def _win(call_api, lead_id, admin_token, partner_token):
    _accept(call_api, lead_id, admin_token)
    status, body = _move_stage(
        call_api, partner_token, lead_id, target_stage="won", confirmed_terminal=True
    )
    assert status == 200, body
    return body["lead"]


def _lose(call_api, lead_id, admin_token, partner_token):
    _accept(call_api, lead_id, admin_token)
    status, body = _move_stage(
        call_api, partner_token, lead_id, target_stage="lost", confirmed_terminal=True
    )
    assert status == 200, body
    return body["lead"]


def _set_settlement(lead_id, status, reviewed_by=None, reason=None):
    """Directly write the settlement axis, simulating a legacy row the
    migration backfilled (or any state a test needs without going through
    the reviewed action itself)."""
    conn = get_conn()
    try:
        conn.execute(
            """UPDATE leads SET settlement_status=?, settlement_reviewed_by=?,
                   settlement_review_reason=?
                WHERE id=?""",
            (status, reviewed_by, reason, lead_id),
        )
        conn.commit()
    finally:
        conn.close()


def _reopen(call_api, token, lead_id, **body):
    body.setdefault("confirmed", True)
    body.setdefault("expected_revision", _revision(lead_id))
    body.setdefault("operation_id", f"op-reopen-{lead_id}-{_revision(lead_id)}")
    return call_api("POST", f"/api/leads/{lead_id}/reopen", token=token, body=body)


def _review(call_api, token, lead_id, **body):
    body.setdefault("confirmed", True)
    body.setdefault("expected_revision", _revision(lead_id))
    body.setdefault("operation_id", f"op-review-{lead_id}-{_revision(lead_id)}")
    return call_api("POST", f"/api/leads/{lead_id}/reopen-eligibility", token=token, body=body)


@pytest.fixture
def manager_user2(test_db_path):
    """A second staff account so authorization tests do not depend on
    whichever manager_user the shared conftest happens to seed elsewhere."""
    password = "ManagerPass123!"
    email = "manager2@test.pl"
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO users (email, password, name, role, level, active)
               VALUES (?,?,?,?,?,1)""",
            (email, bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
             "Test Manager Two", "manager", "bronze"),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    finally:
        conn.close()
    user = dict(row)
    user["plain_password"] = password
    return user


@pytest.fixture
def observer_user(test_db_path):
    """Read-only staff (Observer). Not seeded by the shared conftest --
    local here since this plan is the first to need one for a write-path
    negative test."""
    password = "ObserverPass123!"
    email = "observer@test.pl"
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO users (email, password, name, role, level, active)
               VALUES (?,?,?,?,?,1)""",
            (email, bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
             "Test Observer", "observer", "bronze"),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    finally:
        conn.close()
    user = dict(row)
    user["plain_password"] = password
    return user


# ─── AUTHORIZATION ────────────────────────────────────────────────

class TestReopenAuthorization:
    def test_partner_cannot_reopen(self, call_api, sample_lead, admin_user, partner_user):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        _lose(call_api, lead_id, admin_token, partner_token)

        status, body = _reopen(call_api, partner_token, lead_id, target_stage="contact", reason="Pomyłka.")
        assert status == 403, body
        assert _lead_row(lead_id)["status"] == "lost"

    def test_manager_cannot_reopen(self, call_api, sample_lead, admin_user, partner_user, manager_user2):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        manager_token = _login(call_api, manager_user2)
        lead_id = sample_lead["id"]
        _lose(call_api, lead_id, admin_token, partner_token)

        status, body = _reopen(call_api, manager_token, lead_id, target_stage="contact", reason="Pomyłka.")
        assert status == 403, body
        assert _lead_row(lead_id)["status"] == "lost"

    def test_observer_cannot_reopen(self, call_api, sample_lead, admin_user, partner_user, observer_user):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        observer_token = _login(call_api, observer_user)
        lead_id = sample_lead["id"]
        _lose(call_api, lead_id, admin_token, partner_token)

        status, body = _reopen(call_api, observer_token, lead_id, target_stage="contact", reason="Pomyłka.")
        assert status == 403, body

    def test_manager_cannot_review_settlement_eligibility(
        self, call_api, sample_lead, admin_user, partner_user, manager_user2
    ):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        manager_token = _login(call_api, manager_user2)
        lead_id = sample_lead["id"]
        _win(call_api, lead_id, admin_token, partner_token)

        status, body = _review(call_api, manager_token, lead_id, result="unpaid", reason="Sprawdzone w księgowości.")
        assert status == 403, body
        assert _lead_row(lead_id)["settlement_status"] == "unpaid"  # unchanged from the fresh-win default


# ─── ORDINARY (NO FINANCIAL HISTORY) REOPEN ────────────────────────────────

class TestOrdinaryReopen:
    def test_lost_with_no_financial_history_reopens_directly(
        self, call_api, sample_lead, admin_user, partner_user
    ):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        _lose(call_api, lead_id, admin_token, partner_token)
        assert _lead_row(lead_id)["commission"] is None

        status, body = _reopen(call_api, admin_token, lead_id, target_stage="contact", reason="Klient jednak zainteresowany.")
        assert status == 200, body
        assert body["lead"]["status"] == "contact"
        row = _lead_row(lead_id)
        assert row["status"] == "contact"
        assert row["registration_status"] == "accepted"

    def test_lost_with_financial_history_requires_review_first(
        self, call_api, sample_lead, admin_user, partner_user
    ):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        _lose(call_api, lead_id, admin_token, partner_token)
        # A commission recorded on a lost deal (e.g. a producer correction
        # entered before it closed) still counts as financial history the reopen door
        # gates -- not just a win's own auto-stamped amount.
        conn = get_conn()
        try:
            conn.execute("UPDATE leads SET commission=500 WHERE id=?", (lead_id,))
            conn.commit()
        finally:
            conn.close()

        status, body = _reopen(call_api, admin_token, lead_id, target_stage="contact", reason="Pomyłka.")
        assert status == 409, body
        assert body["code"] == "settlement_unknown"
        assert _lead_row(lead_id)["status"] == "lost"

        status, body = _review(call_api, admin_token, lead_id, result="unpaid", reason="Sprawdzone, nic nie wypłacono.")
        assert status == 200, body
        assert body["lead"]["settlement_status"] == "unpaid"

        status, body = _reopen(call_api, admin_token, lead_id, target_stage="contact", reason="Pomyłka.")
        assert status == 200, body
        assert body["lead"]["status"] == "contact"


# ─── SETTLEMENT ELIGIBILITY: unknown / unpaid / paid ───────────────────────

class TestSettlementEligibility:
    def test_unknown_win_cannot_reopen(self, call_api, sample_lead, admin_user, partner_user):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        _win(call_api, lead_id, admin_token, partner_token)
        # Simulate the legacy state the migration itself produces: a
        # recorded commission with no reviewed provenance.
        _set_settlement(lead_id, "unknown")

        status, body = _reopen(call_api, admin_token, lead_id, target_stage="contact", reason="Pomyłka.")
        assert status == 409, body
        assert body["code"] == "settlement_unknown"
        row = _lead_row(lead_id)
        assert row["status"] == "won"
        assert row["commission"] is not None
        assert row["won_at"] is not None

    def test_explicit_reviewed_unpaid_then_reopen_works(
        self, call_api, sample_lead, admin_user, partner_user
    ):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        _win(call_api, lead_id, admin_token, partner_token)
        _set_settlement(lead_id, "unknown")

        status, body = _review(
            call_api, admin_token, lead_id, result="unpaid",
            reason="Sprawdzono księgowość — nic nie wypłacono.",
        )
        assert status == 200, body
        assert body["lead"]["settlement_status"] == "unpaid"
        row = _lead_row(lead_id)
        assert row["settlement_reviewed_by"] == admin_user["id"]
        assert row["settlement_reviewed_at"]
        assert row["settlement_review_reason"] == "Sprawdzono księgowość — nic nie wypłacono."

        status, body = _reopen(
            call_api, admin_token, lead_id, target_stage="negotiation",
            reason="Klient chce renegocjować warunki.",
        )
        assert status == 200, body
        assert body["lead"]["status"] == "negotiation"
        row = _lead_row(lead_id)
        assert row["won_at"] is None
        assert row["commission"] is None
        assert row["settlement_status"] is None
        assert row["settlement_reviewed_by"] is None
        assert row["settlement_review_reason"] is None

    def test_review_alone_moves_nothing(self, call_api, sample_lead, admin_user, partner_user):
        """A review alone moves nothing -- saving a review
        result must not itself change status/commission/won_at."""
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        _win(call_api, lead_id, admin_token, partner_token)
        _set_settlement(lead_id, "unknown")
        before = _lead_row(lead_id)

        status, body = _review(call_api, admin_token, lead_id, result="unpaid", reason="Sprawdzone.")
        assert status == 200, body

        after = _lead_row(lead_id)
        assert after["status"] == before["status"]
        assert after["commission"] == before["commission"]
        assert after["won_at"] == before["won_at"]

    def test_paid_cannot_switch_to_unpaid_or_reopen(
        self, call_api, sample_lead, admin_user, partner_user
    ):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        _win(call_api, lead_id, admin_token, partner_token)
        _set_settlement(lead_id, "unknown")

        status, body = _review(call_api, admin_token, lead_id, result="paid", reason="Wypłacono w kwietniu.")
        assert status == 200, body
        assert body["lead"]["settlement_status"] == "paid"

        # No flip back to unpaid.
        status, body = _review(call_api, admin_token, lead_id, result="unpaid", reason="Jednak nie.")
        assert status == 409, body
        assert body["code"] == "settlement_paid"
        assert _lead_row(lead_id)["settlement_status"] == "paid"

        # No reopen either.
        status, body = _reopen(call_api, admin_token, lead_id, target_stage="contact", reason="Pomyłka.")
        assert status == 409, body
        assert body["code"] == "settlement_paid"
        assert _lead_row(lead_id)["status"] == "won"

    def test_review_requires_recorded_financial_history(
        self, call_api, sample_lead, admin_user, partner_user
    ):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        _lose(call_api, lead_id, admin_token, partner_token)
        assert _lead_row(lead_id)["commission"] is None

        status, body = _review(call_api, admin_token, lead_id, result="unpaid", reason="Nie dotyczy.")
        assert status == 409, body
        assert body["code"] == "no_financial_history"


# ─── FRESH WIN CYCLE AFTER REOPEN ───────────────────────────────────────────

class TestWinReopenRewinCycle:
    def test_win_reopen_rewin_opens_a_fresh_unpaid_cycle(
        self, call_api, sample_lead, admin_user, partner_user
    ):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        first_win = _win(call_api, lead_id, admin_token, partner_token)
        assert first_win["settlement_status"] == "unpaid"  # fresh win, bounded assumption
        first_commission = first_win["commission"]
        first_won_at = first_win["won_at"]

        status, body = _review(
            call_api, admin_token, lead_id, result="unpaid", reason="Zweryfikowano.",
        )
        assert status == 200, body

        status, body = _reopen(
            call_api, admin_token, lead_id, target_stage="negotiation", reason="Klient wraca do negocjacji.",
        )
        assert status == 200, body

        status, body = _move_stage(
            call_api, partner_token, lead_id, target_stage="won", confirmed_terminal=True,
        )
        assert status == 200, body
        second_win = body["lead"]
        assert second_win["settlement_status"] == "unpaid"
        assert second_win["won_at"] != first_won_at
        assert second_win["commission"] == first_commission  # same deal, same value/rate -> same amount

        # The previous cycle's record stays readable in the immutable history.
        reopen_events = _events(lead_id, "lead.reopen")
        assert len(reopen_events) == 1

    def test_commission_override_pct_survives_reopen(
        self, call_api, sample_lead, admin_user, partner_user
    ):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        status, body = call_api(
            "PATCH", f"/api/leads/{lead_id}",
            token=admin_token,
            body={"commission_override_pct": 12,
                  "expected_revision": _revision(lead_id), "operation_id": "rate-1"},
        )
        assert status == 200, body

        _win(call_api, lead_id, admin_token, partner_token)
        assert _lead_row(lead_id)["commission_override_pct"] == 12

        status, body = _review(call_api, admin_token, lead_id, result="unpaid", reason="OK.")
        assert status == 200, body
        status, body = _reopen(call_api, admin_token, lead_id, target_stage="contact", reason="Pomyłka.")
        assert status == 200, body

        row = _lead_row(lead_id)
        assert row["commission"] is None
        assert row["commission_override_pct"] == 12  # negotiated rate survives, only the amount is cleared


# ─── DESTINATION / PARTNER RE-EDIT / VALIDATION ────────────────────────────

class TestReopenValidation:
    def test_destination_must_be_active_nonterminal_stage(
        self, call_api, sample_lead, admin_user, partner_user
    ):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        _lose(call_api, lead_id, admin_token, partner_token)

        status, body = _reopen(call_api, admin_token, lead_id, target_stage="won", reason="Pomyłka.")
        assert status == 409, body
        assert body["code"] == "target_not_nonterminal"

        status, body = _reopen(call_api, admin_token, lead_id, target_stage="not-a-real-stage", reason="Pomyłka.")
        assert status == 409, body
        assert body["code"] == "unknown_stage"

    def test_reopen_requires_confirmed_true(self, call_api, sample_lead, admin_user, partner_user):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        _lose(call_api, lead_id, admin_token, partner_token)

        status, body = _reopen(
            call_api, admin_token, lead_id, target_stage="contact", reason="Pomyłka.", confirmed=False,
        )
        assert status == 400, body
        assert _lead_row(lead_id)["status"] == "lost"

    def test_reopen_requires_a_reason(self, call_api, sample_lead, admin_user, partner_user):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        _lose(call_api, lead_id, admin_token, partner_token)

        status, body = _reopen(call_api, admin_token, lead_id, target_stage="contact", reason="")
        assert status == 400, body

        status, body = _reopen(call_api, admin_token, lead_id, target_stage="contact", reason="x" * 4001)
        assert status == 400, body

    def test_cannot_reopen_a_lead_that_is_not_terminal(
        self, call_api, sample_lead, admin_user, partner_user
    ):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        _accept(call_api, lead_id, admin_token)

        status, body = _reopen(call_api, admin_token, lead_id, target_stage="negotiation", reason="Nie ma sensu.")
        assert status == 409, body
        assert body["code"] == "not_terminal"

    def test_partner_regains_editor_after_reopen(
        self, call_api, sample_lead, admin_user, partner_user
    ):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        _lose(call_api, lead_id, admin_token, partner_token)

        status, body = call_api("GET", f"/api/leads/{lead_id}", token=partner_token)
        assert status == 200, body

        _reopen(call_api, admin_token, lead_id, target_stage="contact", reason="Pomyłka.")

        status, body = call_api(
            "PATCH", f"/api/leads/{lead_id}/details", token=partner_token,
            body={"client_name": "Nowa nazwa kontaktu",
                  "expected_revision": _revision(lead_id), "operation_id": "details-after-reopen",
                  "schema_revision": lead_fields.schema_version()},
        )
        assert status == 200, body
        assert body["lead"]["client_name"] == "Nowa nazwa kontaktu"


# ─── AUDIT / EVENT TRAIL ──────────────────────────────────────────

class TestReopenAuditTrail:
    def test_actor_reason_source_dest_persisted_in_workflow_history(
        self, call_api, sample_lead, admin_user, partner_user
    ):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        _lose(call_api, lead_id, admin_token, partner_token)

        status, body = _reopen(
            call_api, admin_token, lead_id, target_stage="presentation",
            reason="Klient odezwał się ponownie.",
        )
        assert status == 200, body

        events = _events(lead_id, "lead.reopen")
        assert len(events) == 1
        event = events[0]
        assert event["actor_id"] == admin_user["id"]
        assert "\"from\": \"lost\"" in event["payload_json"]
        assert "\"to\": \"presentation\"" in event["payload_json"]
        assert "Klient odezwał się ponownie." in event["payload_json"]

    def test_reopen_is_audited(self, call_api, sample_lead, admin_user, partner_user, mocker):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        _lose(call_api, lead_id, admin_token, partner_token)

        audit_log = mocker.patch("api.audit.log")
        status, body = _reopen(call_api, admin_token, lead_id, target_stage="contact", reason="Pomyłka producenta.")
        assert status == 200, body
        audit_log.assert_called_once()
        args = audit_log.call_args.args
        assert args[1] == "lead.reopen"
        assert "Pomyłka producenta." in args[4]

    def test_review_result_is_audited(self, call_api, sample_lead, admin_user, partner_user, mocker):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        _win(call_api, lead_id, admin_token, partner_token)
        _set_settlement(lead_id, "unknown")

        audit_log = mocker.patch("api.audit.log")
        status, body = _review(call_api, admin_token, lead_id, result="unpaid", reason="Sprawdzone.")
        assert status == 200, body
        audit_log.assert_called_once()
        args = audit_log.call_args.args
        assert args[1] == "lead.reopen_eligibility"

    def test_blocked_reopen_attempt_leaves_no_audit_trail_claiming_it_happened(
        self, call_api, sample_lead, admin_user, partner_user, mocker
    ):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        _win(call_api, lead_id, admin_token, partner_token)
        _set_settlement(lead_id, "unknown")

        audit_log = mocker.patch("api.audit.log")
        status, body = _reopen(call_api, admin_token, lead_id, target_stage="contact", reason="Pomyłka.")
        assert status == 409, body
        audit_log.assert_not_called()


# ─── OPERATION REPLAY / STALE REVISION (shared workflow core) ─────────────

class TestReopenReplayAndStaleness:
    def test_replaying_the_same_operation_id_returns_the_first_result_without_a_second_write(
        self, call_api, sample_lead, admin_user, partner_user
    ):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        _lose(call_api, lead_id, admin_token, partner_token)

        original_body = {
            "target_stage": "contact", "reason": "Pomyłka.", "confirmed": True,
            "expected_revision": _revision(lead_id), "operation_id": "reopen-once",
        }
        status, first = call_api(
            "POST", f"/api/leads/{lead_id}/reopen", token=admin_token, body=original_body,
        )
        assert status == 200, first
        revision_after_first = _revision(lead_id)

        # A real retry resends the EXACT same request the client originally
        # built (same expected_revision it read, same operation_id) -- not a
        # freshly re-read revision, which would be a different operation
        # that happens to reuse an id (and is correctly refused as a
        # conflict, not silently replayed).
        status, replay = call_api(
            "POST", f"/api/leads/{lead_id}/reopen", token=admin_token, body=dict(original_body),
        )
        assert status == 200, replay
        assert replay == first
        assert _revision(lead_id) == revision_after_first

    def test_stale_revision_is_refused(self, call_api, sample_lead, admin_user, partner_user):
        admin_token = _login(call_api, admin_user)
        partner_token = _login(call_api, partner_user)
        lead_id = sample_lead["id"]
        _lose(call_api, lead_id, admin_token, partner_token)
        stale_revision = _revision(lead_id)

        # Bump the revision under the reopen call's feet.
        conn = get_conn()
        try:
            conn.execute("UPDATE leads SET revision = revision + 1 WHERE id=?", (lead_id,))
            conn.commit()
        finally:
            conn.close()

        status, body = call_api(
            "POST", f"/api/leads/{lead_id}/reopen", token=admin_token,
            body={"target_stage": "contact", "reason": "Pomyłka.", "confirmed": True,
                  "expected_revision": stale_revision, "operation_id": "reopen-stale"},
        )
        assert status == 409, body
        assert body["code"] == "stale_revision"


# ─── MIGRATION IDEMPOTENCY (additive/reversible; legacy + fresh) ──────────

class TestSettlementMigration:
    def test_rerunning_migrate_db_is_a_no_op_on_an_already_migrated_database(self, test_db_path):
        # conftest already migrated this database once.
        conn = get_conn()
        try:
            before = {row[1] for row in conn.execute("SELECT ordinal_position, column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='leads'").fetchall()}
        finally:
            conn.close()
        assert {"settlement_status", "settlement_reviewed_by",
                "settlement_reviewed_at", "settlement_review_reason"} <= before

        db_module.check_schema()  # rerun: must not raise, must not duplicate columns

        conn = get_conn()
        try:
            after = {row[1] for row in conn.execute("SELECT ordinal_position, column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='leads'").fetchall()}
            legacy_won_count = conn.execute(
                "SELECT COUNT(*) FROM leads WHERE commission IS NOT NULL"
            ).fetchone()[0]
        finally:
            conn.close()
        assert after == before
        assert legacy_won_count == 0  # no leads exist yet in this bare fixture
