"""
PartnerType -- every change to who can do what leaves an audit entry.

Promotions, suspensions, passwords set by somebody else and accounts created
through invitations are exactly what an incident review needs to see.
Audit writes are muted suite-wide (conftest), so the recorded call is the
assertion.
"""
from api import audit
from api.invitations import accept_invitation, create_invitation
from api.users import change_password, handle_create, update_user


def _entries(action):
    return [call.args for call in audit.log.call_args_list
            if len(call.args) > 1 and call.args[1] == action]


def test_promoting_an_account_records_the_old_and_new_role(admin_user, manager_user):
    update_user(manager_user["id"], {"role": "admin"}, admin_user)

    (entry,) = _entries("user.update")
    actor, _action, entity, entity_id, detail = entry[:5]
    assert actor["id"] == admin_user["id"]
    assert (entity, entity_id) == ("user", str(manager_user["id"]))
    assert "role: manager → admin" in detail


def test_a_moderator_suspending_a_partner_is_recorded(manager_user, partner_user):
    update_user(partner_user["id"], {"account_status": "suspended"}, manager_user)

    (entry,) = _entries("user.update")
    assert entry[0]["id"] == manager_user["id"]
    assert "account_status: active → suspended" in entry[4]


def test_profile_changes_are_named_without_their_contents(partner_user):
    update_user(partner_user["id"], {"phone": "+48 600 000 111"}, partner_user)

    (entry,) = _entries("user.update")
    assert "phone" in entry[4]
    assert "600 000 111" not in entry[4]


def test_an_update_that_changes_nothing_writes_no_entry(admin_user, partner_user):
    update_user(partner_user["id"], {"level": partner_user["level"]}, admin_user)

    assert _entries("user.update") == []


def test_an_admin_setting_somebody_elses_password_is_recorded(admin_user, partner_user):
    change_password(partner_user["id"], "", "NewAdminSet123!", admin_user)

    (entry,) = _entries("user.password")
    assert entry[0]["id"] == admin_user["id"]
    assert entry[3] == str(partner_user["id"])


def test_creating_an_account_is_recorded(admin_user):
    status, body = handle_create(None, admin_user, {
        "name": "Nowy", "email": "nowy.audit@test.pl", "password": "ValidPass123!",
        "company": "Acme", "role": "manager",
    })

    assert status == 201
    (entry,) = _entries("user.create")
    assert entry[0]["id"] == admin_user["id"]
    assert entry[3] == str(body["user"]["id"])
    assert "role=manager" in entry[4]


def test_an_invitation_records_the_new_account_and_its_acceptance(admin_user):
    user, token = create_invitation(
        {"name": "Zaproszony", "email": "zaproszony.audit@test.pl", "company": "Acme"},
        admin_user,
    )
    (created,) = _entries("user.create")
    assert created[0]["id"] == admin_user["id"]
    assert created[3] == str(user["id"])

    accept_invitation(token, "ValidNewPass123!")

    (accepted,) = _entries("user.invitation_accepted")
    assert accepted[0]["id"] == user["id"]
    assert accepted[3] == str(user["id"])
