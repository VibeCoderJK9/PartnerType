"""
PartnerType — Authorization sweep completeness gate.

Inline role comparisons have no shared symbol, so a missed one is a silent
privilege change with no other single command that catches it — in either
direction. This test reads the source text of every `api/*.py` module and
asserts the set of modules that still carry an inline comparison of a user's
role against the administrator literal (`role ... == "admin"` /
`role ... != "admin"`) equals a hard-coded allowlist, checked both ways: a
module dropping off the list (a guard widened by accident) and a module
appearing that is not on the list (a guard that leaked back to admin-only)
are both failures.

Design: one shared predicate (api/authz.py), and a grep for the literal
string `!= "admin"` — every remaining hit must be on the allowlist. This
test is that grep, made permanent and exhaustive.
"""
import re

import pytest
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[2]

# Modules expected to still carry an inline admin-only role comparison.
# Configuration modules (products, commission_tiers, bonus_defs, lead_stages,
# settings_api, email_service) call the shared can_configure() predicate
# instead, because the moderator may configure everything except the audit
# log and user management. The modules that remain admin-only:
#
#   - users: staff-account administration (the roster, account creation).
#     update_user() also carries the literal, for the narrower rule that
#     only an administrator may set `role` — a moderator may accept, suspend
#     and reject a PARTNER but may never promote anyone.
#   - invitations: the team-invite guard — only an administrator may invite a
#     Moderator or an Observer, so the role cannot mint itself.
#   - agent / agent_tools: the AI-agent surface.
#
#   api/audit.py is deliberately absent: it uses the admin-only
#   can_view_audit() predicate rather than an inline literal.
EXPECTED_ADMIN_ONLY_MODULES = {
    "users",
    "invitations",
    "agent",
    "agent_tools",
}

# Matches user["role"] != "admin", user.get("role") == "admin",
# acting_user["role"] != "admin", etc. -- any inline comparison of a role
# expression against the admin string literal, regardless of the exact
# accessor shape or comparison direction.
ADMIN_LITERAL_RE = re.compile(r"role.{0,10}[!=]=\s*[\"']admin[\"']")


def _strip_comments(source: str) -> str:
    return re.sub(r"(?m)#.*$", "", source)


def _modules_with_inline_admin_comparison():
    found = set()
    for path in sorted(API_DIR.glob("*.py")):
        # api/authz.py is the one module that is SUPPOSED to compare a role
        # against a literal -- being the single place that does so is its
        # entire job. Scanning it would make this test flag the very design
        # it exists to enforce.
        if path.stem == "authz":
            continue
        text = _strip_comments(path.read_text())
        if ADMIN_LITERAL_RE.search(text):
            found.add(path.stem)
    return found


def test_residual_admin_only_guard_set_matches_allowlist_exactly():
    actual = _modules_with_inline_admin_comparison()

    missing = EXPECTED_ADMIN_ONLY_MODULES - actual
    assert not missing, (
        f"Module(s) {sorted(missing)} no longer carry an inline admin-only "
        "role comparison, but the allowlist expects them to -- a "
        "configuration-owning guard may have been widened to admit staff "
        "by accident."
    )

    extra = actual - EXPECTED_ADMIN_ONLY_MODULES
    assert not extra, (
        f"Module(s) {sorted(extra)} now carry an inline admin-only role "
        "comparison but are not on the allowlist -- a guard that should "
        "admit staff may have leaked back to admin-only, or a new "
        "admin-only guard was added outside the classified inventory "
        "."
    )


def test_gateway_modules_carry_no_inline_admin_comparison():
    """The API gateway decides roles only through api/authz.py.

    `API_DIR.glob("*.py")` above does not descend into packages, so the
    gateway would be invisible to the sweep. Every admin-only decision there
    -- the Integracje switches, the overview of every connection, revoking
    someone else's key -- goes through can_manage_integrations(); an inline
    comparison against the admin literal in api/gateway/*.py is a guard that
    bypassed the shared predicate.
    """
    found = []
    for path in sorted(API_DIR.glob("gateway/*.py")):
        text = _strip_comments(path.read_text())
        if ADMIN_LITERAL_RE.search(text):
            found.append(path.stem)
    assert sorted(API_DIR.glob("gateway/*.py")), "the gateway sweep found no modules at all"
    assert not found, f"Inline admin-literal role comparison in api/gateway: {found}"


def test_is_staff_defined_only_in_authz_module():
    """api/authz.py is the single shared predicate every widened guard
    calls. A second definition
    elsewhere would mean a module re-invented its own tuple instead of
    importing the shared one, defeating the whole point of a single
    greppable symbol."""
    hits = []
    for path in sorted(API_DIR.glob("*.py")):
        if path.stem == "authz":
            continue
        if re.search(r"\bdef is_staff\b", path.read_text()):
            hits.append(path.stem)
    assert not hits, f"is_staff redefined outside api/authz.py in: {hits}"


# ─── Canonical configuration writers check the role themselves ──────────────
#
# The HTTP handlers are not the only callers of these writers (the assistant's
# confirmed translations reach them directly), so each writer refuses a role
# that may not configure the programme before it touches anything.

def _a_producer_stage():
    from api import lead_stages
    return next(s for s in lead_stages.get_all() if not s["is_terminal"])


def _a_registration_field():
    from api import registration_fields
    from api.db import get_conn
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO registration_fields (label, label_en, max_length, sort_order, "
            "active, created_at) VALUES ('Pytanie', '', 200, 0, 0, '2026-09-01')"
        )
        conn.commit()
    finally:
        conn.close()
    return registration_fields.get_all()[0]


def _writers():
    from api import lead_stages, registration_fields
    return {
        "update_stage": lambda user: lead_stages.update_stage(
            _a_producer_stage()["id"], {"name_en": "Changed"}, user),
        "delete_stage": lambda user: lead_stages.delete_stage(
            _a_producer_stage()["id"], user),
        "update_field": lambda user: registration_fields.update_field(
            _a_registration_field()["id"], {"label_en": "Changed"}, user),
        "delete_field": lambda user: registration_fields.delete_field(
            _a_registration_field()["id"], user),
    }


@pytest.mark.parametrize("writer", ["update_stage", "delete_stage",
                                    "update_field", "delete_field"])
@pytest.mark.parametrize("role", ["partner", "observer"])
def test_configuration_writers_refuse_a_role_that_may_not_configure(writer, role):
    from api import lead_stages, registration_fields
    stages_before = lead_stages.get_all()

    with pytest.raises(PermissionError):
        _writers()[writer]({"id": 999, "role": role, "name": "Nope"})

    assert lead_stages.get_all() == stages_before
    assert all(f["label_en"] == "" for f in registration_fields.get_all())
