"""Router-level proof that accepted-lead money comes from tiers and bonuses."""

import pytest

from api.commission_tiers import create_tier, update_tier
from api.db import get_conn
from api.tests.integration.conftest import lead_payload


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


def test_admin_acceptance_persists_tier_and_percent_bonus_commission(
    call_api, admin_user, partner_user_tiered, attach_partner_bonus
):
    attach_partner_bonus(partner_user_tiered, "percent", 2)
    partner_token = _login_token(call_api, partner_user_tiered)
    admin_token = _login_token(call_api, admin_user)

    create_status, create_body = call_api(
        "POST",
        "/api/leads",
        body=lead_payload(value=10000),
        token=partner_token,
    )
    assert create_status == 201
    lead_id = create_body["lead"]["id"]

    accept_status, accept_body = call_api(
        "PATCH",
        f"/api/leads/{lead_id}",
        body={"registration_status": "accepted"},
        token=admin_token,
    )

    assert accept_status == 200
    # Accepting shows the figure, winning records it: the
    # tier rate and the active percent bonus are what this test is about,
    # and they now show up in the forecast first.
    assert accept_body["lead"]["commission_projected"] == 900.0

    win_status, win_body = call_api(
        "PATCH",
        f"/api/leads/{lead_id}",
        body={"status": "won"},
        token=admin_token,
    )
    assert win_status == 200
    assert win_body["lead"]["commission"] == 900.0

    conn = get_conn()
    try:
        persisted = conn.execute(
            "SELECT commission FROM leads WHERE id=?", (lead_id,)
        ).fetchone()
    finally:
        conn.close()
    assert persisted["commission"] == 900.0


# ─── commission_tiers.name_en ─────────────────────────────────────
# The producer names his own tiers ("Diament", "Partner Premium"); name_en is
# his own English wording of the same tier, following the *_en +
# Ui.producerLabel pattern already shipped for bonus_definitions. Router-level
# so the whitelist, the role gate and the JSON shape are all proven through the
# same surface a real client uses -- not just the bare function.
#
# The factory default seeds exactly 5 ACTIVE tiers (MAX_TIERS), and
# create_tier's ceiling counts active rows regardless of the new tier's own
# active flag -- so any test that POSTs a brand new tier has to free a slot
# first, exactly as a real producer would have to.
class TestCommissionTierEnglishName:
    def _existing_tier_id(self, call_api, token):
        _, body = call_api("GET", "/api/tiers", token=token)
        return body["tiers"][0]["id"]

    def _make_room(self, call_api, token):
        _, body = call_api("GET", "/api/tiers", token=token)
        active = [t for t in body["tiers"] if t["active"]]
        if len(active) >= 5:
            call_api("PATCH", f"/api/tiers/{active[0]['id']}", body={"active": False}, token=token)

    def test_http_create_list_patch_list_roundtrip(self, call_api, admin_user):
        token = _login_token(call_api, admin_user)
        self._make_room(call_api, token)
        create_status, create_body = call_api(
            "POST", "/api/tiers",
            body={"name": "Diament VIP", "name_en": "Diamond", "base_rate": 12, "color": "#123456"},
            token=token,
        )
        assert create_status == 201
        tier_id = create_body["tier"]["id"]
        assert create_body["tier"]["name_en"] == "Diamond"

        list_status, list_body = call_api("GET", "/api/tiers", token=token)
        assert list_status == 200
        listed = next(t for t in list_body["tiers"] if t["id"] == tier_id)
        assert listed["name_en"] == "Diamond"

        patch_status, patch_body = call_api(
            "PATCH", f"/api/tiers/{tier_id}", body={"name_en": "Diamond Tier"}, token=token,
        )
        assert patch_status == 200
        assert patch_body["tier"]["name_en"] == "Diamond Tier"

        list_status2, list_body2 = call_api("GET", "/api/tiers", token=token)
        listed2 = next(t for t in list_body2["tiers"] if t["id"] == tier_id)
        assert listed2["name_en"] == "Diamond Tier"

    def test_empty_string_clears_only_the_english_name(self, call_api, admin_user):
        token = _login_token(call_api, admin_user)
        tier_id = self._existing_tier_id(call_api, token)
        _, seeded = call_api("PATCH", f"/api/tiers/{tier_id}", body={"name_en": "Ruby"}, token=token)
        original_name = seeded["tier"]["name"]

        status, patched = call_api(
            "PATCH", f"/api/tiers/{tier_id}", body={"name_en": ""}, token=token,
        )
        assert status == 200
        assert patched["tier"]["name_en"] == ""
        assert patched["tier"]["name"] == original_name

    def test_missing_key_preserves_the_previous_translation(self, call_api, admin_user):
        token = _login_token(call_api, admin_user)
        tier_id = self._existing_tier_id(call_api, token)
        call_api("PATCH", f"/api/tiers/{tier_id}", body={"name_en": "Opal EN"}, token=token)

        status, patched = call_api(
            "PATCH", f"/api/tiers/{tier_id}", body={"base_rate": 8}, token=token,
        )
        assert status == 200
        assert patched["tier"]["name_en"] == "Opal EN"
        assert patched["tier"]["base_rate"] == 8

    def test_old_payload_without_english_field_still_creates(self, call_api, admin_user):
        token = _login_token(call_api, admin_user)
        self._make_room(call_api, token)
        status, body = call_api(
            "POST", "/api/tiers",
            body={"name": "Legacy", "base_rate": 5, "color": "#111111"},
            token=token,
        )
        assert status == 201
        assert body["tier"]["name_en"] == ""

    def test_partner_is_refused_both_create_and_translate(self, call_api, partner_user, admin_user):
        admin_token = _login_token(call_api, admin_user)
        tier_id = self._existing_tier_id(call_api, admin_token)

        partner_token = _login_token(call_api, partner_user)
        # Permission is checked before the 5-active-tier ceiling, so a
        # refused partner's request never needs a free slot either.
        create_status, _ = call_api(
            "POST", "/api/tiers",
            body={"name": "Hack", "name_en": "Hack", "base_rate": 1, "color": "#000000"},
            token=partner_token,
        )
        assert create_status == 403

        patch_status, _ = call_api(
            "PATCH", f"/api/tiers/{tier_id}", body={"name_en": "Hacked"}, token=partner_token,
        )
        assert patch_status == 403

    def test_english_name_over_limit_is_refused(self, call_api, admin_user):
        token = _login_token(call_api, admin_user)
        tier_id = self._existing_tier_id(call_api, token)
        status, _ = call_api(
            "PATCH", f"/api/tiers/{tier_id}", body={"name_en": "x" * 101}, token=token,
        )
        assert status == 400

    def test_manager_may_translate_a_tier(self, call_api, manager_user, admin_user):
        admin_token = _login_token(call_api, admin_user)
        tier_id = self._existing_tier_id(call_api, admin_token)
        status, body = call_api(
            "PATCH", f"/api/tiers/{tier_id}", body={"name_en": "Managed"},
            token=_login_token(call_api, manager_user),
        )
        assert status == 200
        assert body["tier"]["name_en"] == "Managed"

    def test_canonical_create_and_update_enforce_configure_rights_directly(self, partner_user):
        """a caller that reaches the canonical function directly
        (the assistant's tool layer, or any future adapter) must not bypass
        the HTTP handler's 403 -- can_configure is enforced here too."""
        with pytest.raises(PermissionError):
            create_tier({"name": "Direct", "name_en": "Direct"}, partner_user)
        with pytest.raises(PermissionError):
            update_tier(1, {"name_en": "Direct"}, partner_user)
