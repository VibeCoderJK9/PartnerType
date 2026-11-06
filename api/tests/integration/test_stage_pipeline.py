"""
PartnerType -- Stage-pipeline-driven backend behavior.

Proves the backend decides lead-lifecycle behavior (validation, commission,
notifications, bulk actions, statistics) from the configurable lead_stages
table rather than from hardcoded status words. Every request is driven
through api.router.dispatch via the call_api fixture; stage setup uses the
real api.lead_stages functions (already covered unit-level in
test_lead_stages.py) rather than re-testing stage CRUD here.
"""
from api import lead_stages
from api import notifications
from api.db import get_conn
from api.tests.integration.conftest import lead_payload


# Capture the real implementation before the autouse mute_side_effects
# fixture replaces it, so notification-content tests can restore it.
REAL_CREATE_NOTIFICATION = notifications.create_notification


def _login_token(call_api, user):
    _, body = call_api(
        "POST",
        "/api/auth/login",
        body={
            "email": user["email"],
            "password": user["plain_password"],
            "role": user["role"],
        },
    )
    return body["token"]


def _rename_terminal_in_the_database(stage_id, name):
    """Rename a terminal behind the API's back.

    Win/Lose are fixed: ``update_stage`` refuses
    to touch a terminal at all -- but a database written before that lock can
    still hold a renamed one, and the point being proven here is that
    behaviour keys off ``kind``, never off the label. So the rename is done
    in SQL, which is exactly the state such a database is in.
    """
    with get_conn() as conn:
        conn.execute("UPDATE lead_stages SET name=? WHERE id=?", (name, stage_id))
        conn.commit()


def _accept(call_api, lead_id, admin_token):
    """Put a lead into the sales pipeline. A deal must be
    accepted before it can occupy any sales stage at all, so every test below
    that positions a lead has to make the producer's decision first."""
    status, body = call_api(
        "PATCH",
        f"/api/leads/{lead_id}",
        body={"registration_status": "accepted"},
        token=admin_token,
    )
    assert status == 200, body
    return body


def _notification_rows():
    conn = get_conn()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM notifications ORDER BY id"
        ).fetchall()]
    finally:
        conn.close()


# ─── 1. NEW PRODUCER-DEFINED STAGES ────────────────────────────────────────

class TestProducerDefinedStages:
    def test_moving_to_a_new_producer_stage_is_rejected_before_creation_then_succeeds(
        self, call_api, admin_user, partner_user
    ):
        partner_token = _login_token(call_api, partner_user)
        admin_token = _login_token(call_api, admin_user)

        _, create_body = call_api(
            "POST", "/api/leads", body=lead_payload(), token=partner_token
        )
        lead_id = create_body["lead"]["id"]
        _accept(call_api, lead_id, admin_token)

        # Rejected before the stage exists.
        status, body = call_api(
            "PATCH",
            f"/api/leads/{lead_id}",
            body={"status": "negocjacje"},
            token=admin_token,
        )
        assert status == 400
        assert "error" in body

        lead_stages.create_stage({"name": "Negocjacje"}, admin_user)

        # Succeeds once the producer stage exists.
        status, body = call_api(
            "PATCH",
            f"/api/leads/{lead_id}",
            body={"status": "negocjacje"},
            token=admin_token,
        )
        assert status == 200
        assert body["lead"]["status"] == "negocjacje"

    def test_filtering_by_a_producer_defined_stage_returns_only_matching_leads(
        self, call_api, admin_user, partner_user
    ):
        stage = lead_stages.create_stage({"name": "Weryfikacja"}, admin_user)
        partner_token = _login_token(call_api, partner_user)
        admin_token = _login_token(call_api, admin_user)

        _, first = call_api(
            "POST", "/api/leads", body=lead_payload(nip="1111111111"), token=partner_token
        )
        _, second = call_api(
            "POST", "/api/leads",
            body=lead_payload(nip="2222222222", website="second-example.pl"),
            token=partner_token,
        )
        first_id = first["lead"]["id"]
        second_id = second["lead"]["id"]
        _accept(call_api, first_id, admin_token)
        _accept(call_api, second_id, admin_token)

        call_api(
            "PATCH", f"/api/leads/{first_id}",
            body={"status": stage["key"]}, token=admin_token,
        )

        status, body = call_api(
            "GET", f"/api/leads?status={stage['key']}", token=admin_token
        )

        assert status == 200
        ids = [lead["id"] for lead in body["leads"]]
        assert ids == [first_id]
        assert second_id not in ids


# ─── 2. COMMISSION FOLLOWS THE WIN TERMINAL, NOT A MOVABLE FLAG ────────────

class TestCommissionOnTheWinTerminal:
    def test_commission_is_projected_at_every_stage_and_recorded_on_the_win(
        self, call_api, admin_user, partner_user_tiered
    ):
        """End to end: the commission is visible at every stage but not
        payable until the win."""
        partner_token = _login_token(call_api, partner_user_tiered)
        admin_token = _login_token(call_api, admin_user)

        _, create_body = call_api(
            "POST", "/api/leads", body=lead_payload(value=10000), token=partner_token
        )
        lead_id = create_body["lead"]["id"]
        accepted = _accept(call_api, lead_id, admin_token)

        assert accepted["lead"]["commission_projected"] == 700.0
        assert accepted["lead"]["commission"] is None

        # Walk every non-terminal stage: the forecast holds, nothing is
        # recorded.
        stages = [s for s in lead_stages.get_all() if not s["is_terminal"]]
        for stage in stages:
            status, body = call_api(
                "PATCH", f"/api/leads/{lead_id}",
                body={"status": stage["key"]}, token=admin_token,
            )
            assert status == 200
            assert body["lead"]["commission_projected"] == 700.0
            assert body["lead"]["commission"] is None, (
                f"commission recorded too early at {stage['key']!r}"
            )

        win = next(s for s in lead_stages.get_all() if s["kind"] == "win")
        status, body = call_api(
            "PATCH", f"/api/leads/{lead_id}",
            body={"status": win["key"]}, token=admin_token,
        )
        assert status == 200
        assert body["lead"]["commission"] == 700.0

    def test_a_renamed_win_terminal_still_records_the_commission(
        self, call_api, admin_user, partner_user_tiered
    ):
        """Behaviour keys off the terminal's `kind`, never off its label, so
        a producer renaming "Wygrany" cannot silently stop commissions."""
        win = next(s for s in lead_stages.get_all() if s["kind"] == "win")
        _rename_terminal_in_the_database(win["id"], "Sprzedane i podpisane")

        partner_token = _login_token(call_api, partner_user_tiered)
        admin_token = _login_token(call_api, admin_user)
        _, create_body = call_api(
            "POST", "/api/leads", body=lead_payload(value=10000), token=partner_token
        )
        lead_id = create_body["lead"]["id"]
        _accept(call_api, lead_id, admin_token)

        status, body = call_api(
            "PATCH", f"/api/leads/{lead_id}",
            body={"status": win["key"]}, token=admin_token,
        )

        assert status == 200
        assert body["lead"]["commission"] == 700.0

    def test_winning_twice_does_not_recompute_an_existing_commission(
        self, call_api, admin_user, partner_user_tiered
    ):
        partner_token = _login_token(call_api, partner_user_tiered)
        admin_token = _login_token(call_api, admin_user)
        _, create_body = call_api(
            "POST", "/api/leads", body=lead_payload(value=10000), token=partner_token
        )
        lead_id = create_body["lead"]["id"]
        _accept(call_api, lead_id, admin_token)

        call_api("PATCH", f"/api/leads/{lead_id}",
                 body={"commission": 123.45}, token=admin_token)
        status, body = call_api("PATCH", f"/api/leads/{lead_id}",
                                body={"status": "won"}, token=admin_token)

        # A recorded commission is a decision, not a cache.
        assert status == 200
        assert body["lead"]["commission"] == 123.45


# ─── 3. NOTIFICATIONS KEY OFF THE AXIS, NOT THE WORD ───────────────────────

class TestNotificationsByAxis:
    def test_every_registration_decision_notifies_the_owning_partner(
        self, call_api, admin_user, partner_user
    ):
        notifications.create_notification.side_effect = REAL_CREATE_NOTIFICATION
        partner_token = _login_token(call_api, partner_user)
        admin_token = _login_token(call_api, admin_user)

        seen = {}
        # "needs_info" is not a valid domain label; each lead just needs its
        # own website so the duplicate queue does not conflate them.
        # Separate clients: a partner can no longer submit the same client
        # (same NIP) twice.
        nips = {"needs_info": "3333333333", "rejected": "4444444444", "accepted": "5555555555"}
        websites = {
            "needs_info": "wymaga-informacji.pl",
            "rejected": "odrzucony.pl",
            "accepted": "zaakceptowany.pl",
        }
        for decision in ("needs_info", "rejected", "accepted"):
            _, create_body = call_api(
                "POST", "/api/leads",
                body=lead_payload(nip=nips[decision], website=websites[decision]),
                token=partner_token,
            )
            assert "lead" in create_body, create_body
            lead_id = create_body["lead"]["id"]
            # A refusal or a request for information owes the partner words;
            #acceptance needs none.
            decision_body = {"registration_status": decision}
            if decision in ("rejected", "needs_info"):
                decision_body["registration_message"] = "Powód decyzji."
            call_api("PATCH", f"/api/leads/{lead_id}",
                     body=decision_body, token=admin_token)
            seen[decision] = _notification_rows()[-1]

        assert seen["accepted"]["type"] == "success"
        assert seen["rejected"]["type"] == "error"
        assert seen["needs_info"]["type"] == "info"
        # Three different messages, not one generic "lead changed".
        assert len({row["title"] for row in seen.values()}) == 3

    def test_a_renamed_lose_terminal_still_notifies(
        self, call_api, admin_user, partner_user
    ):
        notifications.create_notification.side_effect = REAL_CREATE_NOTIFICATION
        lose = next(s for s in lead_stages.get_all() if s["kind"] == "lose")
        _rename_terminal_in_the_database(lose["id"], "Klient wybral konkurencje")

        partner_token = _login_token(call_api, partner_user)
        admin_token = _login_token(call_api, admin_user)
        _, create_body = call_api(
            "POST", "/api/leads", body=lead_payload(), token=partner_token
        )
        lead_id = create_body["lead"]["id"]
        _accept(call_api, lead_id, admin_token)
        before = len(_notification_rows())

        call_api("PATCH", f"/api/leads/{lead_id}",
                 body={"status": lose["key"]}, token=admin_token)

        assert len(_notification_rows()) == before + 1


# ─── 4. BULK ACTIONS ACT ON THE REGISTRATION AXIS ──────────────────────────

class TestBulkActionsAreRegistrationDecisions:
    def test_bulk_accept_and_reject_set_the_registration_status(
        self, call_api, admin_user, partner_user
    ):
        partner_token = _login_token(call_api, partner_user)
        admin_token = _login_token(call_api, admin_user)

        _, first = call_api("POST", "/api/leads",
                            body=lead_payload(nip="1111111111"), token=partner_token)
        _, second = call_api("POST", "/api/leads",
                             body=lead_payload(nip="2222222222", website="second.pl"),
                             token=partner_token)
        first_id, second_id = first["lead"]["id"], second["lead"]["id"]

        call_api("POST", "/api/leads/bulk",
                 body={"action": "accept", "items": [{"id": first_id,
                       "expected_revision": first["lead"]["revision"], "operation_id": "first"}]}, token=admin_token)
        call_api("POST", "/api/leads/bulk",
                 body={"action": "reject", "items": [{"id": second_id,
                       "expected_revision": second["lead"]["revision"], "operation_id": "second",
                       "message": "Brak zgodności"}]}, token=admin_token)

        _, body = call_api("GET", "/api/leads", token=admin_token)
        by_id = {lead["id"]: lead for lead in body["leads"]}
        assert by_id[first_id]["registration_status"] == "accepted"
        assert by_id[second_id]["registration_status"] == "rejected"

    def test_bulk_accept_survives_a_producer_reshaping_the_sales_pipeline(
        self, call_api, admin_user, partner_user
    ):
        """The old implementation hunted the pipeline for whichever stage
        carried the commission trigger and returned 400 when it found none.
        A registration decision cannot fail that way."""
        for stage in lead_stages.get_all():
            if stage["kind"] == "progress":
                lead_stages.update_stage(
                    stage["id"], {"name": f"Etap {stage['id']}"}, admin_user
                )

        partner_token = _login_token(call_api, partner_user)
        admin_token = _login_token(call_api, admin_user)
        _, created = call_api("POST", "/api/leads", body=lead_payload(), token=partner_token)

        status, body = call_api(
            "POST", "/api/leads/bulk",
            body={"action": "accept", "items": [{"id": created["lead"]["id"],
                  "expected_revision": created["lead"]["revision"], "operation_id": "reshaped"}]},
            token=admin_token,
        )

        assert status == 200
        assert len(body["ok"]) == 1
        assert body["error"] == []


# ─── 5. STATS DERIVE FROM THE REGISTRATION AXIS ────────────────────────────

class TestStatsDeriveFromTheRegistrationAxis:
    def test_every_lead_lands_in_exactly_one_bucket_including_a_won_deal(
        self, call_api, admin_user, partner_user
    ):
        """The gap this replaces: a lead sitting on a win stage used to match
        no bucket at all -- counted in `total`, invisible in every per-bucket
        figure on both dashboards."""
        partner_token = _login_token(call_api, partner_user)
        admin_token = _login_token(call_api, admin_user)

        ids = []
        for index in range(4):
            _, created = call_api(
                "POST", "/api/leads",
                body=lead_payload(nip=str(index + 1) * 10, website=f"lead{index}.pl"),
                token=partner_token,
            )
            ids.append(created["lead"]["id"])

        # One of each: pending (untouched), needs_info, rejected, and a deal
        # accepted then won.
        call_api("PATCH", f"/api/leads/{ids[1]}",
                 body={"registration_status": "needs_info",
                       "registration_message": "Prosimy o uzupełnienie."}, token=admin_token)
        call_api("PATCH", f"/api/leads/{ids[2]}",
                 body={"registration_status": "rejected",
                       "registration_message": "Powód odrzucenia."}, token=admin_token)
        _accept(call_api, ids[3], admin_token)
        call_api("PATCH", f"/api/leads/{ids[3]}",
                 body={"status": "won"}, token=admin_token)

        status, stats = call_api("GET", "/api/stats", token=admin_token)

        assert status == 200
        assert stats["total_leads"] == 4
        buckets = (
            stats["new_leads"] + stats["pending_leads"]
            + stats["accepted_leads"] + stats["rejected_leads"]
        )
        assert buckets == stats["total_leads"]
        assert stats["accepted_leads"] == 1, "the won deal must still count as accepted"


class TestUnknownStageKeyRefused:
    def test_unknown_status_on_update_is_refused_in_polish(
        self, call_api, admin_user, partner_user
    ):
        partner_token = _login_token(call_api, partner_user)
        admin_token = _login_token(call_api, admin_user)

        _, create_body = call_api(
            "POST", "/api/leads", body=lead_payload(), token=partner_token
        )
        lead_id = create_body["lead"]["id"]

        status, body = call_api(
            "PATCH", f"/api/leads/{lead_id}",
            body={"status": "nieistniejacy_etap"}, token=admin_token,
        )

        assert status == 400
        assert "error" in body
        assert "status" in body["error"]

    def test_unknown_status_on_filter_is_refused(self, call_api, admin_user):
        admin_token = _login_token(call_api, admin_user)

        status, body = call_api(
            "GET", "/api/leads?status=nieistniejacy_etap", token=admin_token
        )

        assert status == 400
        assert "error" in body


# ─── PARTNER KANBAN MOVE HONORS THE LIVE CONFIGURED PIPELINE ───────
#
# The partner's own board move (api/leads.py::move_partner_lead) reads
# lead_stages exactly like every other stage-aware path in this module: a
# stage the producer adds after this code was written is immediately a
# valid, in-order destination, with no code change and no hardcoded key.

class TestPartnerMoveHonorsProducerDefinedStages:
    def test_a_stage_added_after_this_code_was_written_is_a_valid_destination(
        self, call_api, admin_user, partner_user
    ):
        stage = lead_stages.create_stage({"name": "Kwalifikacja"}, admin_user)
        partner_token = _login_token(call_api, partner_user)
        admin_token = _login_token(call_api, admin_user)

        _, create_body = call_api(
            "POST", "/api/leads", body=lead_payload(), token=partner_token
        )
        lead_id = create_body["lead"]["id"]
        _accept(call_api, lead_id, admin_token)
        revision = get_conn().execute(
            "SELECT revision FROM leads WHERE id=?", (lead_id,)
        ).fetchone()["revision"]

        status, body = call_api(
            "PATCH", f"/api/leads/{lead_id}/stage", token=partner_token,
            body={"target_stage": stage["key"], "expected_revision": revision,
                  "operation_id": "custom-stage-move"},
        )

        assert status == 200, body
        assert body["lead"]["status"] == stage["key"]

    def test_the_partner_move_reads_the_producers_configured_order_not_a_fixed_list(
        self, call_api, admin_user, partner_user
    ):
        """The board's column order is whatever lead_stages.get_all() returns
        right now; proving the endpoint accepts a move to every currently
        active nonterminal stage in that live order is what backs the
        frontend's "columns ordered producer config" requirement."""
        partner_token = _login_token(call_api, partner_user)
        admin_token = _login_token(call_api, admin_user)

        _, create_body = call_api(
            "POST", "/api/leads", body=lead_payload(), token=partner_token
        )
        lead_id = create_body["lead"]["id"]
        _accept(call_api, lead_id, admin_token)

        nonterminal_keys = [s["key"] for s in lead_stages.get_all() if not s["is_terminal"]]
        for key in nonterminal_keys:
            conn = get_conn()
            revision = conn.execute(
                "SELECT revision FROM leads WHERE id=?", (lead_id,)
            ).fetchone()["revision"]
            conn.close()
            status, body = call_api(
                "PATCH", f"/api/leads/{lead_id}/stage", token=partner_token,
                body={"target_stage": key, "expected_revision": revision,
                      "operation_id": f"walk-{key}"},
            )
            assert status == 200, body
            assert body["lead"]["status"] == key
