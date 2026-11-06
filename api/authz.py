"""
PartnerType — Shared role predicates.

The single greppable place every guard calls.
Adding a role to a capability means editing one tuple here, not hand-repeating
a role comparison at each call site — that repetition has no way to prove
completeness, which is what `api/tests/unit/test_authz_sweep.py` exists to
enforce.

Four roles, three of them staff-side:

  admin     Vendor — owns the program. Everything, with no exception.
  manager   Moderator — runs the channel day to day: full Settings, full
            operations, full commission corrections — but NOT the audit log
            and NOT user management.
  observer  Observer — read-only on leads and nothing else; the rest of the
            app is shown greyed out.
  partner   The other side of the program entirely; never staff.

Two capabilities are deliberately NARROWER than `is_staff` rather than being
folded into it — they are the two things the moderator role does not get:

  can_view_audit    admin only. A moderator handling customers must not read
                    the tamper-evidence trail of their own actions.
  can_manage_users  admin only. A moderator cannot mint another moderator,
                    nor promote themselves — the containment that makes the
                    role safe to hand out.

Dependency-free by design: importable from any handler module without risk of
a circular import.
"""

# Operational staff: leads, partners, commissions, comments. Both roles that
# actually run the program day to day.
STAFF_ROLES = ("admin", "manager")

# Who may change the program's configuration — products, tiers, bonus
# definitions, lead stages, company/SMTP settings. Today this is the same set
# as STAFF_ROLES; it stays a separate tuple because it answers a different
# question, and the two sets may diverge.
CONFIG_ROLES = ("admin", "manager")

# Who may read leads at all. Every role in the product does -- but a
# partner is additionally row-scoped inside the query itself
# (api/leads.py::get_leads filters l.partner_id to their own id), so this
# tuple gates reaching the endpoint, not what comes back from it. It exists
# so that a role added later has to be listed here deliberately instead of
# inheriting full lead visibility by omission.
LEAD_READ_ROLES = ("admin", "manager", "observer", "partner")


def _role(user):
    if not user:
        return None
    return user.get("role")


def is_staff(user) -> bool:
    """True only when `user` is truthy and its role is one of STAFF_ROLES."""
    return _role(user) in STAFF_ROLES


def can_configure(user) -> bool:
    """May change program configuration (Settings surfaces). Admits the
    moderator."""
    return _role(user) in CONFIG_ROLES


def can_manage_users(user) -> bool:
    """May list, create, edit or invite staff accounts. Admin only — the
    reason a moderator cannot escalate."""
    return _role(user) == "admin"


def can_manage_integrations(user) -> bool:
    """May open the instance to outside AI tools. Admin only.

    Switching integrations on, letting partners connect, choosing which lead
    fields leave the instance, setting limits, and seeing or revoking every
    key and OAuth connection. The moderator configures the programme
    (can_configure) but must not be able to open the producer's data to an
    external AI -- that is the account owner's decision, and so is the GDPR
    acknowledgement that goes with it.
    """
    return _role(user) == "admin"


def can_view_audit(user) -> bool:
    """May read the audit log. Admin only."""
    return _role(user) == "admin"


def can_handle_data_requests(user) -> bool:
    """May export or erase one person's data. Owner only.

    Narrower than the audit log on purpose: this is the one surface that can
    remove a client's record from the system, and a moderator handling day-to-day
    customers has no business holding it."""
    return _role(user) == "admin"


def can_configure_lead_fields(user) -> bool:
    """
    May define what a lead submission asks for.

    Admin-only for now, and deliberately its own capability rather than a
    reuse of can_configure: this is a conservative mapping of new scope, and
    it must not quietly widen or narrow the Settings surfaces the moderator
    already has. Admitting the moderator later is a one-line change here plus
    its tests, not a hunt through every guard.
    """
    return _role(user) in ("admin",)


def can_pay_commission(user) -> bool:
    """
    May record that a partner's commission was paid, or reverse such a record
   .

    Admin-only, deliberately narrower than is_staff(). A moderator handles the
    customer relationship -- accepting deals, chasing partners -- but declaring
    money paid is the account owner's act, and it is the one write in this app
    that asserts something happened OUTSIDE the system (a transfer) which the
    app can never verify. ARCHITECTURE.md forbids payouts to the AI agent for the
    same reason. Its own capability rather than
    a reuse of can_reopen_lead: those two doors happen to share a role today
    and must be able to diverge without one silently dragging the other.
    """
    return _role(user) == "admin"


def can_reopen_lead(user) -> bool:
    """
    May pull a mistakenly won/lost lead back into an editable, active,
    nonterminal stage.

    Admin-only, deliberately narrower than is_staff(): this door reopens a
    financial terminal state (the shared _terminal_exit_attempted guard) that
    every other write
    path is blocked from -- a moderator granted the same access as the
    account that owns the program would widen who can touch a closed deal's
    money, not just its pipeline position. Its own capability rather than a
    reuse of can_manage_users/can_view_audit, because it answers a different
    question.
    """
    return _role(user) == "admin"


def can_assign_lead(user) -> bool:
    """
    May create a lead directly for a chosen partner.

    Admin-only, deliberately narrower than is_staff(), for the same reason
    can_reopen_lead() is: this door writes a lead that is already accepted
    into the sale on somebody else's account, which is the one creation path
    that skips the producer's own decision step. A moderator keeps the
    decision they already have (accept/reject a partner's submission) --
    widening this would let them originate the deal and approve it in a
    single action, with the commission attached to a partner they picked.
    Its own capability rather than a reuse of can_configure_lead_fields:
    admitting the moderator later is a one-line change here plus its tests.
    """
    return _role(user) == "admin"


def can_read_leads(user) -> bool:
    """May reach the lead read endpoints. Write paths keep calling
    is_staff(), so an observer reads and never writes; a partner reaching a
    read endpoint still only sees their own rows."""
    return _role(user) in LEAD_READ_ROLES


def is_partner(user) -> bool:
    """True for a partner account. Used for the partner's own actions on
    their own submissions; the handler still checks that the lead is theirs."""
    return _role(user) == "partner"


def is_staff_or_partner(user) -> bool:
    """Staff, or a partner acting on their own records. Excludes the
    read-only observer, who reads leads and nothing else."""
    return is_staff(user) or is_partner(user)


def can_use_assistant(user) -> bool:
    """May talk to the AI assistant: the administrator and partners."""
    return _role(user) in ("admin", "partner")


# The predicates an HTTP route may name as its access rule in
# api/router.py. A route rule is either "public" (no session needed),
# "authenticated" (any signed-in role; the handler scopes the data itself),
# or one of these names. Listing them explicitly keeps the route table from
# referring to a helper that was never meant to gate a request.
ROUTE_PREDICATES = {
    fn.__name__: fn
    for fn in (
        is_staff,
        is_partner,
        is_staff_or_partner,
        can_configure,
        can_configure_lead_fields,
        can_manage_users,
        can_manage_integrations,
        can_view_audit,
        can_handle_data_requests,
        can_pay_commission,
        can_reopen_lead,
        can_assign_lead,
        can_read_leads,
        can_use_assistant,
    )
}
