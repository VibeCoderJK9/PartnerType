"""
PartnerType -- Users module unit tests.

Breadth-first coverage across the entire user lifecycle: listing/filtering,
creation, update (self vs admin), password change, self-service registration,
password-disclosure guarantees, and every HTTP handler entry point.
"""
import bcrypt
import pytest

from api.auth import login
from api.db import get_conn
from api.users import (
    _user_pub,
    _CREATE_FIELDS,
    _REGISTER_FIELDS,
    list_users,
    list_team,
    get_user,
    create_user,
    update_user,
    change_password,
    AccountExists,
    register_partner,
    handle_list,
    handle_create,
    handle_get,
    handle_update,
    handle_password,
    handle_register,
)


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _assert_no_password(d):
    """Shared assertion reused across every user-returning path so a future
    fourth return path cannot quietly skip the check."""
    assert "password" not in d


def _insert_user(
    email, name, company="Acme", role="partner", level="bronze",
    status="active", active=1, created_at=None, password="RawPass123!",
):
    """Insert a user row directly with full control over every field,
    including created_at, so ordering and filter tests are deterministic."""
    conn = get_conn()
    try:
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        cols = ["email", "password", "name", "role", "company", "level", "account_status", "active"]
        vals = [email, hashed, name, role, company, level, status, active]
        if created_at:
            cols.append("created_at")
            vals.append(created_at)
        placeholders = ",".join(["?"] * len(vals))
        conn.execute(
            f"INSERT INTO users ({','.join(cols)}) VALUES ({placeholders})", vals
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    finally:
        conn.close()

    d = dict(row)
    d["plain_password"] = password
    return d


VALID_CREATE_DATA = {
    "name": "New User",
    "email": "new.user@test.pl",
    "password": "ValidPass123!",
    "company": "Acme",
    # Phone is a mandatory registration field, like name, company and
    # e-mail, so a payload without one is not valid.
    "phone": "+48 600 100 200",
}


# ─── LISTING ──────────────────────────────────────────────────────────────────

class TestListUsers:
    def test_unfiltered_listing_returns_all_newest_first(self, test_db_path):
        _insert_user("a@test.pl", "Alice", created_at="2020-01-01T00:00:00")
        _insert_user("b@test.pl", "Bob", created_at="2020-01-02T00:00:00")
        _insert_user("c@test.pl", "Carol", created_at="2020-01-03T00:00:00")

        emails = [u["email"] for u in list_users()]
        assert emails == ["c@test.pl", "b@test.pl", "a@test.pl"]

    def test_role_filter_narrows_to_one_role(self, test_db_path):
        _insert_user("p1@test.pl", "P1", role="partner")
        _insert_user("adm1@test.pl", "Adm1", role="admin")

        result = list_users(role_filter="admin")
        assert all(u["role"] == "admin" for u in result)
        assert any(u["email"] == "adm1@test.pl" for u in result)
        assert not any(u["email"] == "p1@test.pl" for u in result)

    def test_status_filter_narrows_correctly(self, test_db_path):
        _insert_user("act@test.pl", "Active", status="active")
        _insert_user("pen@test.pl", "Pending", status="pending")

        result = list_users(status_filter="pending")
        assert all(u["account_status"] == "pending" for u in result)
        assert any(u["email"] == "pen@test.pl" for u in result)
        assert not any(u["email"] == "act@test.pl" for u in result)

    def test_free_text_filter_matches_name(self, test_db_path):
        _insert_user("x@test.pl", "Zbigniew Findable", company="Other Co")
        _insert_user("y@test.pl", "Someone Else", company="Other Co")

        result = list_users(q="Findable")
        assert len(result) == 1
        assert result[0]["email"] == "x@test.pl"

    def test_free_text_filter_matches_email(self, test_db_path):
        _insert_user("findableuser@test.pl", "Someone")
        _insert_user("other@test.pl", "Someone Else")

        result = list_users(q="findableuser")
        assert len(result) == 1
        assert result[0]["email"] == "findableuser@test.pl"

    def test_free_text_filter_matches_company(self, test_db_path):
        _insert_user("z@test.pl", "Someone", company="FindableCorp")
        _insert_user("w@test.pl", "Other", company="Other Co")

        result = list_users(q="FindableCorp")
        assert len(result) == 1
        assert result[0]["email"] == "z@test.pl"

    def test_combined_filters_intersect(self, test_db_path):
        _insert_user("m1@test.pl", "Match One", role="admin", status="active")
        _insert_user("m2@test.pl", "Match Two", role="partner", status="active")
        _insert_user("m3@test.pl", "Match Three", role="admin", status="pending")

        result = list_users(role_filter="admin", status_filter="active")
        assert [u["email"] for u in result] == ["m1@test.pl"]


# ─── CREATE ───────────────────────────────────────────────────────────────────

class TestCreateUser:
    def test_valid_creation_returns_public_shape(self, test_db_path):
        result = create_user(dict(VALID_CREATE_DATA, email="New.User@Test.PL"))
        assert result["name"] == "New User"
        assert result["email"] == "new.user@test.pl"

    @pytest.mark.parametrize("missing_field", ["name", "email", "password", "company"])
    def test_missing_required_field_rejected(self, test_db_path, missing_field):
        data = dict(VALID_CREATE_DATA, email="req@test.pl")
        data[missing_field] = ""
        with pytest.raises(ValueError):
            create_user(data)

    def test_duplicate_email_rejected(self, test_db_path):
        create_user(dict(VALID_CREATE_DATA, email="dup@test.pl"))
        with pytest.raises(ValueError):
            create_user(dict(VALID_CREATE_DATA, name="Second", email="dup@test.pl"))

    def test_defaults_role_level_and_status(self, test_db_path):
        result = create_user(dict(VALID_CREATE_DATA, email="def@test.pl"))
        assert result["role"] == "partner"
        assert result["level"] == "bronze"
        assert result["account_status"] == "active"

    def test_stored_password_verifies_with_bcrypt(self, test_db_path):
        create_user(dict(VALID_CREATE_DATA, email="hash@test.pl"))

        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT password FROM users WHERE email = ?", ("hash@test.pl",)
            ).fetchone()
        finally:
            conn.close()

        assert bcrypt.checkpw(b"ValidPass123!", row["password"].encode())

    def test_out_of_range_role_raises_value_error(self, test_db_path):
        with pytest.raises(ValueError):
            create_user(dict(VALID_CREATE_DATA, email="badrole@test.pl", role="superadmin"))

    @pytest.mark.parametrize(
        "field,value",
        [
            ("name", 123),
            ("name", "x" * 201),
            ("email", "not-an-email"),
            ("password", []),
            ("password", "x" * 73),
            ("password", "🔒" * 19),
            ("company", "x" * 201),
            ("phone", "x" * 51),
            ("bio", "x" * 2001),
            ("level", "diamond"),
        ],
    )
    def test_invalid_create_values_fail_before_connection(
        self, test_db_path, mocker, field, value
    ):
        mocked_conn = mocker.patch("api.users.get_conn")

        with pytest.raises(ValueError):
            create_user(dict(VALID_CREATE_DATA, **{field: value}))

        mocked_conn.assert_not_called()

    def test_unknown_create_field_fails_before_connection(self, test_db_path, mocker):
        mocked_conn = mocker.patch("api.users.get_conn")

        with pytest.raises(ValueError, match="unexpected"):
            create_user(dict(VALID_CREATE_DATA, unexpected=True))

        mocked_conn.assert_not_called()


# ─── CREATE/REGISTER FIELD-ALLOWLIST CONTRACT ──────────────────────────────────
# The backend half of the frontend/backend payload contract; the browser half
# lives in js/__tests__/users-create-contract.test.js.

class TestCreateFieldsContract:
    def test_create_fields_contains_account_status(self):
        assert "account_status" in _CREATE_FIELDS

    def test_register_fields_excludes_account_status(self):
        assert "account_status" not in _REGISTER_FIELDS

    @pytest.mark.parametrize(
        "status,expected_active",
        [("active", 1), ("pending", 0), ("suspended", 0)],
    )
    def test_create_user_stores_requested_status_and_mirrors_active(
        self, test_db_path, status, expected_active
    ):
        email = f"{status}.contract@test.pl"
        result = create_user(
            dict(VALID_CREATE_DATA, email=email, account_status=status)
        )
        assert result["account_status"] == status
        assert result["active"] == expected_active

        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT account_status, active FROM users WHERE email = ?", (email,)
            ).fetchone()
        finally:
            conn.close()

        assert row["account_status"] == status
        assert row["active"] == expected_active


# ─── UPDATE ───────────────────────────────────────────────────────────────────

class TestUpdateUser:
    def test_admin_can_change_other_users_profile_fields(self, admin_user, partner_user):
        result = update_user(
            partner_user["id"],
            {"name": "Updated Name", "company": "Updated Co", "phone": "123456", "bio": "Updated bio"},
            admin_user,
        )
        assert result["name"] == "Updated Name"
        assert result["company"] == "Updated Co"
        assert result["phone"] == "123456"
        assert result["bio"] == "Updated bio"

    def test_self_can_change_own_profile_fields(self, partner_user):
        result = update_user(
            partner_user["id"],
            {"name": "Self Name", "company": "Self Co", "phone": "999999", "bio": "Self bio"},
            partner_user,
        )
        assert result["name"] == "Self Name"
        assert result["company"] == "Self Co"
        assert result["phone"] == "999999"
        assert result["bio"] == "Self bio"

    def test_non_admin_changing_other_user_raises_permission_error(
        self, partner_user, partner_user_bronze
    ):
        with pytest.raises(PermissionError):
            update_user(partner_user_bronze["id"], {"name": "Hack"}, partner_user)

    def test_admin_can_change_level_status_and_role(self, admin_user, partner_user):
        result = update_user(
            partner_user["id"],
            {"level": "platinum", "account_status": "suspended", "role": "admin"},
            admin_user,
        )
        assert result["level"] == "platinum"
        assert result["account_status"] == "suspended"
        assert result["role"] == "admin"

    def test_non_admin_privileged_fields_ignored_but_permitted_apply(self, partner_user):
        result = update_user(
            partner_user["id"],
            {"name": "Allowed Name", "level": "platinum", "role": "admin", "account_status": "suspended"},
            partner_user,
        )
        assert result["name"] == "Allowed Name"
        assert result["level"] == partner_user["level"]
        assert result["role"] == "partner"
        assert result["account_status"] == "active"

    def test_status_suspended_mirrors_active_flag_to_zero(self, admin_user, partner_user):
        result = update_user(partner_user["id"], {"account_status": "suspended"}, admin_user)
        assert result["active"] == 0

    def test_status_active_mirrors_active_flag_to_one(self, admin_user, partner_user):
        update_user(partner_user["id"], {"account_status": "suspended"}, admin_user)
        result = update_user(partner_user["id"], {"account_status": "active"}, admin_user)
        assert result["active"] == 1

    @pytest.mark.parametrize(
        "data",
        [
            {"name": 1},
            {"name": "   "},
            {"company": "x" * 201},
            {"phone": []},
            {"bio": "x" * 2001},
            {"level": "diamond"},
            {"account_status": "banned"},
            {"role": "superadmin"},
            {"nonsense_field": "x"},
        ],
    )
    def test_invalid_admin_update_fails_before_connection(
        self, admin_user, partner_user, mocker, data
    ):
        mocked_conn = mocker.patch("api.users.get_conn")

        with pytest.raises(ValueError):
            update_user(partner_user["id"], data, admin_user)

        mocked_conn.assert_not_called()

    def test_update_trims_profile_fields_without_html_stripping(
        self, admin_user, partner_user
    ):
        literal = "<b>Partner & Co</b>"
        result = update_user(
            partner_user["id"], {"company": f"  {literal}  "}, admin_user
        )
        assert result["company"] == literal

    def test_update_with_no_recognised_field_raises_value_error(self, admin_user, partner_user):
        with pytest.raises(ValueError):
            update_user(partner_user["id"], {"nonsense_field": "x"}, admin_user)

    def test_update_unknown_id_raises_value_error(self, admin_user):
        with pytest.raises(ValueError):
            update_user(999999, {"name": "X"}, admin_user)


# ─── CHANGE PASSWORD ──────────────────────────────────────────────────────────

class TestChangePassword:
    def test_self_service_requires_correct_current_password(self, partner_user):
        change_password(
            partner_user["id"], partner_user["plain_password"], "NewValidPass123!", partner_user
        )

        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT password FROM users WHERE id = ?", (partner_user["id"],)
            ).fetchone()
        finally:
            conn.close()

        assert bcrypt.checkpw(b"NewValidPass123!", row["password"].encode())
        assert not bcrypt.checkpw(b"InvalidPassword123!", row["password"].encode())

    def test_self_service_rejects_wrong_current_password(self, partner_user):
        with pytest.raises(ValueError):
            change_password(partner_user["id"], "WrongCurrent!", "NewValidPass123!", partner_user)

    def test_admin_changing_own_password_must_give_the_current_one(self, admin_user):
        with pytest.raises(ValueError):
            change_password(admin_user["id"], "", "NewOwnerPass123!", admin_user)
        with pytest.raises(ValueError):
            change_password(admin_user["id"], "WrongCurrent!", "NewOwnerPass123!", admin_user)

        conn = get_conn()
        try:
            stored = conn.execute(
                "SELECT password FROM users WHERE id = ?", (admin_user["id"],)
            ).fetchone()["password"]
        finally:
            conn.close()
        assert bcrypt.checkpw(admin_user["plain_password"].encode(), stored.encode())

    def test_admin_changing_own_password_with_the_current_one_succeeds(self, admin_user):
        change_password(
            admin_user["id"], admin_user["plain_password"], "NewOwnerPass123!", admin_user
        )
        user, _token = login(admin_user["email"], "NewOwnerPass123!", "admin")
        assert user["id"] == admin_user["id"]

    def test_admin_can_change_others_password_without_current(self, admin_user, partner_user):
        change_password(partner_user["id"], "", "NewAdminSet123!", admin_user)

        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT password FROM users WHERE id = ?", (partner_user["id"],)
            ).fetchone()
        finally:
            conn.close()

        assert bcrypt.checkpw(b"NewAdminSet123!", row["password"].encode())

    def test_new_password_shorter_than_8_rejected(self, partner_user):
        with pytest.raises(ValueError):
            change_password(
                partner_user["id"], partner_user["plain_password"], "short", partner_user
            )

    @pytest.mark.parametrize("new_password", [None, 123, [], "x" * 73])
    def test_invalid_new_password_fails_before_connection(
        self, partner_user, mocker, new_password
    ):
        mocked_conn = mocker.patch("api.users.get_conn")

        with pytest.raises(ValueError):
            change_password(
                partner_user["id"], partner_user["plain_password"], new_password,
                partner_user,
            )

        mocked_conn.assert_not_called()

    def test_wrong_type_current_password_fails_before_connection(
        self, partner_user, mocker
    ):
        mocked_conn = mocker.patch("api.users.get_conn")

        with pytest.raises(ValueError):
            change_password(
                partner_user["id"], [], "NewValidPass123!", partner_user
            )

        mocked_conn.assert_not_called()

    def test_successful_change_deletes_all_sessions(self, partner_user):
        _, token = login(partner_user["email"], partner_user["plain_password"], "partner")

        conn = get_conn()
        try:
            before_count = conn.execute(
                "SELECT COUNT(*) AS c FROM sessions WHERE user_id = ?", (partner_user["id"],)
            ).fetchone()["c"]
        finally:
            conn.close()
        assert before_count == 1

        change_password(
            partner_user["id"], partner_user["plain_password"], "NewValidPass123!", partner_user
        )

        conn = get_conn()
        try:
            after_count = conn.execute(
                "SELECT COUNT(*) AS c FROM sessions WHERE user_id = ?", (partner_user["id"],)
            ).fetchone()["c"]
        finally:
            conn.close()
        assert after_count == 0

    def test_old_password_no_longer_authenticates_new_does(self, partner_user):
        change_password(
            partner_user["id"], partner_user["plain_password"], "NewValidPass123!", partner_user
        )

        with pytest.raises(ValueError):
            login(partner_user["email"], partner_user["plain_password"], "partner")

        user, token = login(partner_user["email"], "NewValidPass123!", "partner")
        assert token is not None


# ─── REGISTER ─────────────────────────────────────────────────────────────────

class TestRegisterPartner:
    def test_valid_registration_creates_pending_inactive_partner(self, test_db_path):
        register_partner(dict(VALID_CREATE_DATA, email="reg@test.pl"))

        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE email = ?", ("reg@test.pl",)
            ).fetchone()
        finally:
            conn.close()

        assert row["account_status"] == "pending"
        assert row["active"] == 0
        assert row["role"] == "partner"

    def test_registered_account_cannot_login_until_activated(self, test_db_path):
        register_partner(dict(VALID_CREATE_DATA, email="reg2@test.pl"))

        with pytest.raises(ValueError):
            login("reg2@test.pl", VALID_CREATE_DATA["password"], "partner")

    def test_password_shorter_than_8_rejected(self, test_db_path):
        with pytest.raises(ValueError):
            register_partner(dict(VALID_CREATE_DATA, email="shortpw@test.pl", password="short"))

    @pytest.mark.parametrize("missing_field", ["name", "email", "password", "company"])
    def test_missing_required_field_rejected(self, test_db_path, missing_field):
        data = dict(VALID_CREATE_DATA, email="regreq@test.pl")
        data[missing_field] = ""
        with pytest.raises(ValueError):
            register_partner(data)

    def test_existing_email_still_pays_for_a_password_hash(self, test_db_path, monkeypatch):
        register_partner(dict(VALID_CREATE_DATA, email="regtiming@test.pl"))
        calls = []
        real_hashpw = bcrypt.hashpw
        monkeypatch.setattr(bcrypt, "hashpw", lambda *a: calls.append(1) or real_hashpw(*a))
        with pytest.raises(AccountExists):
            register_partner(dict(VALID_CREATE_DATA, email="regtiming@test.pl"))
        assert calls, "a known address must cost a hash, like a new one"

    def test_duplicate_email_rejected(self, test_db_path):
        register_partner(dict(VALID_CREATE_DATA, email="regdup@test.pl"))
        with pytest.raises(ValueError):
            register_partner(dict(VALID_CREATE_DATA, name="Second Reg", email="regdup@test.pl"))

    @pytest.mark.parametrize(
        "field,value",
        [
            ("name", {}),
            ("email", "invalid"),
            ("password", "x" * 73),
            ("company", "x" * 201),
            ("phone", "x" * 51),
            ("bio", "x" * 2001),
        ],
    )
    def test_invalid_registration_fails_before_connection(
        self, test_db_path, mocker, field, value
    ):
        mocked_conn = mocker.patch("api.users.get_conn")

        with pytest.raises(ValueError):
            register_partner(dict(VALID_CREATE_DATA, **{field: value}))

        mocked_conn.assert_not_called()

    def test_registration_rejects_role_override_before_connection(
        self, test_db_path, mocker
    ):
        mocked_conn = mocker.patch("api.users.get_conn")

        with pytest.raises(ValueError, match="role"):
            register_partner(dict(VALID_CREATE_DATA, role="admin"))

        mocked_conn.assert_not_called()


# ─── DISCLOSURE ───────────────────────────────────────────────────────────────

class TestNoPasswordDisclosure:
    def test_public_serializer_is_allowlisted_against_secret_fields(self):
        row = {
            "id": 7,
            "email": "safe@test.pl",
            "name": "Safe User",
            "role": "partner",
            "company": "Acme",
            "level": "bronze",
            "phone": "123",
            "bio": "bio",
            "account_status": "active",
            "active": 1,
            "created_at": "2026-08-22T00:00:00",
            "password": "hash",
            "smtp_password": "smtp-secret",
            "session_token": "session-secret",
            "plain_password": "plain-secret",
        }

        public = _user_pub(row)

        assert set(public) == {
            "id", "email", "name", "role", "company", "level", "phone", "bio",
            "account_status", "active", "created_at",
        }

    def test_public_serializer_allowlists_tier_name_en_but_not_secrets(self):
        """get_user's join adds tier_id/tier_name/tier_color/
        tier_name_en to the row -- _user_pub must let the English tier name
        through while still excluding every private field."""
        row = {
            "id": 7, "email": "safe@test.pl", "name": "Safe User", "role": "partner",
            "company": "Acme", "level": "bronze", "phone": "123", "bio": "bio",
            "account_status": "active", "active": 1, "created_at": "2026-08-22T00:00:00",
            "tier_id": 3, "tier_name": "Gold", "tier_color": "#ffcc00",
            "tier_name_en": "Gold Tier",
            "password": "hash", "smtp_password": "smtp-secret",
            "session_token": "session-secret", "plain_password": "plain-secret",
        }

        public = _user_pub(row)

        assert public["tier_name_en"] == "Gold Tier"
        for secret in ("password", "smtp_password", "session_token", "plain_password"):
            assert secret not in public

    def test_list_users_never_includes_password(self, partner_user):
        for row in list_users():
            _assert_no_password(row)

    def test_get_user_never_includes_password(self, partner_user):
        _assert_no_password(get_user(partner_user["id"]))

    def test_get_user_carries_the_commission_tier(self, partner_user):
        """The profile screen names the tier from here, never from `level`."""
        result = get_user(partner_user["id"])
        assert result["tier_id"] == partner_user["tier_id"]
        assert result["tier_name"] == "Gold"

    def test_get_user_reports_no_tier_as_none(self, partner_user, db_conn):
        db_conn.execute(
            "UPDATE users SET tier_id = NULL WHERE id = ?", (partner_user["id"],)
        )
        db_conn.commit()
        result = get_user(partner_user["id"])
        assert result["tier_id"] is None
        assert result["tier_name"] is None

    # ─── tier_name_en on get_user + the public allowlist ─────────
    # The profile screen (js/profile.js) reads Ui.producerLabel(user,
    # 'tier_name'), which needs tier_name_en on the SAME row as tier_name.

    def test_get_user_carries_the_tier_english_name(self, partner_user, db_conn):
        db_conn.execute(
            "UPDATE commission_tiers SET name_en = 'Gold Tier' WHERE id = ?",
            (partner_user["tier_id"],),
        )
        db_conn.commit()
        result = get_user(partner_user["id"])
        assert result["tier_name_en"] == "Gold Tier"

    def test_get_user_reports_empty_tier_english_name_when_untranslated(self, partner_user):
        """The seeded Gold tier has no English name yet -- empty, not None,
        matching Ui.producerLabel's "not translated yet" contract."""
        result = get_user(partner_user["id"])
        assert result["tier_name_en"] == ""

    def test_get_user_reports_no_tier_english_name_as_none_alongside_no_tier(self, partner_user, db_conn):
        db_conn.execute(
            "UPDATE users SET tier_id = NULL WHERE id = ?", (partner_user["id"],)
        )
        db_conn.commit()
        result = get_user(partner_user["id"])
        assert result["tier_name_en"] is None

    def test_create_user_never_includes_password(self, test_db_path):
        result = create_user(dict(VALID_CREATE_DATA, email="nodisc@test.pl"))
        _assert_no_password(result)

    def test_update_user_never_includes_password(self, admin_user, partner_user):
        result = update_user(partner_user["id"], {"name": "Disclosure Check"}, admin_user)
        _assert_no_password(result)


# ─── TEAM ROSTER ──────────────────────────────────────────────────
# The producer's own team: administrators and channel managerowie together, a
# partner never among them. api.users.list_team() is the read;
# handle_list's scope=team branch is the HTTP surface, gated by the same
# administrator-only guard handle_list already carries -- no new guard code;
# a channel manager cannot reach Settings at all.

class TestListTeam:
    def test_returns_admin_and_manager_omits_partner(self, admin_user):
        _insert_user("mgr@test.pl", "Team Member", role="manager")
        _insert_user("partner@test.pl", "A Partner", role="partner")

        emails = {u["email"] for u in list_team()}
        assert admin_user["email"] in emails
        assert "mgr@test.pl" in emails
        assert "partner@test.pl" not in emails

    def test_single_seeded_admin_returns_exactly_one_row(self, admin_user):
        result = list_team()
        assert len(result) == 1
        assert result[0]["email"] == admin_user["email"]

    def test_admins_precede_managers_and_id_breaks_name_ties(self, admin_user):
        # Two managers sharing a name -- id is the only thing that can hold
        # their order fixed across reloads.
        _insert_user("m1@test.pl", "Tied Name", role="manager")
        _insert_user("m2@test.pl", "Tied Name", role="manager")
        _insert_user("adm2@test.pl", "Zzz Admin", role="admin")

        result = list_team()
        roles = [u["role"] for u in result]
        first_manager_index = roles.index("manager")
        assert all(r == "admin" for r in roles[:first_manager_index])
        assert all(r == "manager" for r in roles[first_manager_index:])

        tied = [u for u in result if u["name"] == "Tied Name"]
        assert [u["email"] for u in tied] == ["m1@test.pl", "m2@test.pl"]

    def test_returns_public_projection_no_password(self, admin_user):
        _insert_user("mgr2@test.pl", "Another Member", role="manager")
        for row in list_team():
            _assert_no_password(row)


class TestHandleListTeamScope:
    def test_scope_team_returns_admin_and_manager_omits_partner(self, admin_user):
        _insert_user("mgr3@test.pl", "Scoped Member", role="manager")
        _insert_user("partner2@test.pl", "Scoped Partner", role="partner")

        status, body = handle_list(None, admin_user, {"scope": ["team"]})
        assert status == 200
        emails = {u["email"] for u in body["users"]}
        assert "mgr3@test.pl" in emails
        assert "partner2@test.pl" not in emails

    def test_scope_team_refuses_partner(self, partner_user):
        status, body = handle_list(None, partner_user, {"scope": ["team"]})
        assert status == 403
        assert "error" in body

    def test_scope_team_refuses_manager(self, manager_user):
        status, body = handle_list(None, manager_user, {"scope": ["team"]})
        assert status == 403
        assert "error" in body

    def test_default_scope_unaffected_by_the_team_branch(self, admin_user):
        _insert_user("mgr4@test.pl", "Default Scope Check", role="manager")

        status, body = handle_list(None, admin_user, {})
        assert status == 200
        assert any(u["email"] == admin_user["email"] for u in body["users"])
        assert any(u["email"] == "mgr4@test.pl" for u in body["users"])


# ─── HANDLER BOUNDARY ─────────────────────────────────────────────────────────

class TestHandlerBoundaries:
    def test_handle_list_refuses_partner(self, partner_user):
        status, body = handle_list(None, partner_user, {})
        assert status == 403
        assert "error" in body

    def test_handle_create_refuses_partner(self, partner_user):
        status, body = handle_create(None, partner_user, dict(VALID_CREATE_DATA, email="hc1@test.pl"))
        assert status == 403
        assert "error" in body

    def test_handle_create_maps_value_error_to_400(self, admin_user):
        status, body = handle_create(None, admin_user, {"name": "", "email": "", "password": "", "company": ""})
        assert status == 400
        assert "error" in body

    def test_handle_create_returns_201_on_success(self, admin_user):
        status, body = handle_create(None, admin_user, dict(VALID_CREATE_DATA, email="hc2@test.pl"))
        assert status == 201
        assert "user" in body

    def test_handle_get_allows_self(self, partner_user):
        status, body = handle_get(None, partner_user, partner_user["id"])
        assert status == 200
        assert "user" in body

    def test_handle_get_refuses_other_user(self, partner_user, partner_user_bronze):
        status, body = handle_get(None, partner_user, partner_user_bronze["id"])
        assert status == 403
        assert "error" in body

    def test_handle_get_lets_a_manager_read_a_partner(self, manager_user, partner_user):
        status, body = handle_get(None, manager_user, partner_user["id"])
        assert status == 200
        assert body["user"]["id"] == partner_user["id"]

    @pytest.mark.parametrize("role", ["admin", "manager", "observer"])
    def test_handle_get_hides_staff_accounts_from_a_manager(self, manager_user, role):
        other = _insert_user(f"other-{role}@test.pl", "Other", role=role, status="pending", active=0)
        status, body = handle_get(None, manager_user, other["id"])
        assert status == 403
        assert "user" not in body

    def test_handle_get_lets_a_manager_read_themselves(self, manager_user):
        status, body = handle_get(None, manager_user, manager_user["id"])
        assert status == 200

    def test_handle_get_lets_an_admin_read_a_staff_account(self, admin_user, manager_user):
        status, body = handle_get(None, admin_user, manager_user["id"])
        assert status == 200

    def test_handle_get_returns_404_for_unknown_id(self, admin_user):
        status, body = handle_get(None, admin_user, 999999)
        assert status == 404
        assert "error" in body

    def test_handle_update_maps_permission_error_to_403(self, partner_user, partner_user_bronze):
        status, body = handle_update(None, partner_user, partner_user_bronze["id"], {"name": "X"})
        assert status == 403
        assert "error" in body

    def test_handle_update_maps_value_error_to_400(self, admin_user, partner_user):
        status, body = handle_update(None, admin_user, partner_user["id"], {"nonsense_field": "x"})
        assert status == 400
        assert "error" in body

    def test_handle_password_maps_permission_failure_to_403(self, partner_user, partner_user_bronze):
        status, body = handle_password(
            None, partner_user, partner_user_bronze["id"],
            {"current_password": "x", "new_password": "NewValidPass123!"},
        )
        assert status == 403
        assert "error" in body

    def test_handle_password_maps_validation_failure_to_400(self, partner_user):
        status, body = handle_password(
            None, partner_user, partner_user["id"],
            {"current_password": "WrongCurrent!", "new_password": "NewValidPass123!"},
        )
        assert status == 400
        assert "error" in body

    def test_handle_register_returns_201_on_success(self, test_db_path):
        status, body = handle_register(None, dict(VALID_CREATE_DATA, email="hreg@test.pl"))
        assert status == 201
        assert body.get("ok") is True

    def test_handle_register_returns_400_on_validation_failure(self, test_db_path):
        status, body = handle_register(None, {"name": "", "email": "", "password": "", "company": ""})
        assert status == 400
        assert "error" in body


class TestEntryTierForPartnersWithoutOne:
    """A self-registered partner approved without a tier would win a deal
    for 0,00 zl, with no warning anywhere."""

    @staticmethod
    def _entry_tier_id():
        conn = get_conn()
        try:
            return conn.execute(
                "SELECT id FROM commission_tiers WHERE active = 1 "
                "ORDER BY sort_order, base_rate, id LIMIT 1"
            ).fetchone()["id"]
        finally:
            conn.close()

    def test_approving_a_self_registered_partner_lands_him_on_the_entry_tier(self, admin_user):
        register_partner({"name": "Kamil Bak", "email": "kamil@bak.example", "company": "Bak Digital",
                          "phone": "+48 700 800 900", "password": "Kamil-Test-2026!"})
        conn = get_conn()
        try:
            new_id = conn.execute("SELECT id, tier_id FROM users WHERE email = 'kamil@bak.example'").fetchone()
        finally:
            conn.close()
        assert new_id["tier_id"] is None, "a pending registration carries no tier yet"

        approved = update_user(new_id["id"], {"account_status": "active"}, admin_user)

        assert approved["tier_id"] == self._entry_tier_id()

    def test_a_partner_created_without_a_tier_gets_the_entry_tier(self):
        created = create_user({"name": "Nowy Partner", "email": "nowy@partner.example", "company": "Nowa sp.",
                               "password": "Nowy-Partner-2026!", "role": "partner", "tier_id": None})

        assert created["tier_id"] == self._entry_tier_id()

    def test_approval_never_overrides_a_tier_the_producer_already_chose(self, admin_user, partner_user):
        update_user(partner_user["id"], {"account_status": "suspended"}, admin_user)

        restored = update_user(partner_user["id"], {"account_status": "active"}, admin_user)

        assert restored["tier_id"] == partner_user["tier_id"]


class TestRegistrationThrottle:
    """No flood of fake partners: 30 sign-ups in 7 s must not mean 30
    notifications."""

    class _Handler:
        def __init__(self, ip):
            self.client_address = (ip, 50000)

    @staticmethod
    def _body(index):
        return {"name": f"Bot {index}", "email": f"bot{index}@spam.example", "company": "Spam",
                "phone": "+48 700 000 000", "password": "Spam-Spam-2026!"}

    def test_an_address_may_register_only_a_few_partners_per_window(self):
        handler = self._Handler("203.0.113.7")
        codes = [handle_register(handler, self._body(i))[0] for i in range(7)]

        assert codes[:5] == [201] * 5
        assert codes[5:] == [429, 429]

    def test_another_address_is_not_blocked_by_the_first(self):
        for i in range(5):
            handle_register(self._Handler("203.0.113.7"), self._body(i))

        status, _ = handle_register(self._Handler("198.51.100.9"), self._body(99))

        assert status == 201

    def test_registrations_never_count_as_failed_logins(self, partner_user):
        for i in range(5):
            handle_register(self._Handler("203.0.113.7"), self._body(i))

        user, _token = login(partner_user["email"], partner_user["plain_password"], "partner",
                             ip="203.0.113.7")

        assert user["email"] == partner_user["email"]
