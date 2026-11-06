"""
PartnerType — API Router
Maps URL paths + methods to handler functions.
"""

import json
import logging
import re
import traceback
import uuid
from urllib.parse import urlparse, parse_qs

from api import authz
from api.i18n import N_, language_from_header, t, use_language
from api.postgres import no_context, system_context, user_context

from api.auth     import (
    get_session_user, handle_login, handle_logout, handle_logout_everywhere,
    handle_me, handle_mfa_login,
)
from api.mfa import (
    handle_backup_codes as mfa_backup_codes,
    handle_begin   as mfa_begin,
    handle_confirm as mfa_confirm,
    handle_disable as mfa_disable,
    handle_reset   as mfa_reset,
    handle_status  as mfa_status,
    is_enabled     as mfa_is_enabled,
    is_required_for as mfa_is_required_for,
)
from api.password_reset import (
    handle_request  as password_reset_request,
    handle_complete as password_reset_complete,
)
from api.leads    import handle_list   as leads_list,   handle_get   as leads_get, \
                         handle_create as leads_create, handle_update as leads_update, \
                         handle_precheck as leads_precheck, \
                         handle_assign as leads_assign, \
                         handle_registration as leads_registration, \
                         handle_information as leads_information, \
                         handle_details as leads_details, \
                         handle_move_stage as leads_move_stage, \
                         handle_reopen as leads_reopen, \
                         handle_reopen_eligibility as leads_reopen_eligibility
from api.partners import handle_list   as partners_list, handle_get  as partners_get
from api.stats    import handle_stats
from api.users    import handle_list   as users_list,   handle_create as users_create, \
                         handle_get    as users_get,    handle_update as users_update, \
                         handle_password as users_password, handle_register as users_register, \
                         handle_delete as users_delete
from api.invitations import (
    handle_create             as invitations_create,
    handle_create_team_invite as invitations_create_team,
    handle_preview            as invitations_preview,
    handle_accept             as invitations_accept,
)
from api.notifications import handle_list as notif_list, handle_mark_read as notif_mark_read, \
                              handle_delete as notif_delete
from api.registration_fields import (
    handle_list        as regfields_list,
    handle_public_list as regfields_public,
    handle_create      as regfields_create,
    handle_update      as regfields_update,
    handle_delete      as regfields_delete,
)
from api.export       import handle_export_leads, handle_export_commissions
from api.validation   import ValidationError
from api.comments     import handle_list as comments_list, handle_create as comments_create, \
                              handle_delete as comments_delete
from api.settings_api import handle_get  as settings_get,  handle_update as settings_update
from api.branding import (
    handle_public    as branding_public,
    handle_logo_file as branding_logo_file,
    handle_upload    as branding_upload,
    handle_delete    as branding_delete,
)
from api.knowledge import (
    MAX_UPLOAD_BODY_BYTES as KNOWLEDGE_MAX_BODY,
    handle_list     as knowledge_list,
    handle_upload   as knowledge_upload,
    handle_update   as knowledge_update,
    handle_delete   as knowledge_delete,
    handle_download as knowledge_download,
)
from api.audit        import handle_list as audit_list
from api.gdpr import (
    handle_export          as gdpr_export,
    handle_erasure_preview as gdpr_erasure_preview,
    handle_erase           as gdpr_erase,
)
from api.leads        import handle_bulk as leads_bulk
from api.lead_fields  import handle_list   as lead_fields_list, \
                            handle_create as lead_fields_create, \
                            handle_update as lead_fields_update
from api.commission_tiers import (
    handle_list   as tiers_list,
    handle_create as tiers_create,
    handle_update as tiers_update,
    handle_delete as tiers_delete,
)
from api.blocks import (
    handle_list   as blocks_list,
    handle_update as blocks_update,
    handle_run    as blocks_run,
)
from api.products import (
    handle_list   as products_list,
    handle_create as products_create,
    handle_update as products_update,
    handle_delete as products_delete,
)
from api.lead_stages import (
    handle_list   as stages_list,
    handle_create as stages_create,
    handle_update as stages_update,
    handle_delete as stages_delete,
)
from api.bonus_defs import (
    handle_list   as bdefs_list,
    handle_create as bdefs_create,
    handle_update as bdefs_update,
    handle_delete as bdefs_delete,
)
from api.partner_bonuses import (
    handle_list_partners as pb_list,
    handle_get_partner   as pb_get,
    handle_assign        as pb_assign,
    handle_remove        as pb_remove,
    handle_update_tier   as pb_tier,
    handle_bulk          as pb_bulk,
)
from api.bonus_awards import (
    handle_list as bonus_awards_list,
    handle_grant as bonus_awards_grant,
    handle_revoke as bonus_awards_revoke,
)
from api.payouts import (
    handle_outstanding         as payout_outstanding,
    handle_pending_wins        as payout_pending_wins,
    handle_confirm_wins        as payout_confirm_wins,
    handle_evidence            as payout_evidence,
    handle_partner_outstanding as payout_partner,
    handle_partner_history     as payout_history,
    handle_my_earnings         as payout_my_earnings,
    handle_record              as payout_record,
    handle_reverse             as payout_reverse,
)
from api.agent import (
    handle_chat as agent_chat,
    handle_history as agent_history,
    handle_reset as agent_reset,
    handle_status as agent_status,
)
from api.agent_actions import (
    handle_cancel as agent_action_cancel,
    handle_confirm as agent_action_confirm,
)
from api.gateway.oauth import (
    handle_request_get      as oauth_request_get,
    handle_request_decision as oauth_request_decision,
)
from api.gateway.admin_api import (
    handle_settings_get       as integrations_settings_get,
    handle_settings_update    as integrations_settings_update,
    handle_me                 as integrations_me,
    handle_key_create         as integrations_key_create,
    handle_key_revoke         as integrations_key_revoke,
    handle_connection_revoke  as integrations_connection_revoke,
    handle_admin_connections  as integrations_admin_connections,
    handle_admin_log          as integrations_admin_log,
    handle_admin_usage        as integrations_admin_usage,
)
from api.affiliate import handle_route as affiliate_route
from api.http_security import (
    DEFAULT_ALLOWED_ORIGINS,
    MAX_JSON_BODY_BYTES,
    extract_session_cookie,
    unsafe_request_is_trusted,
)


logger = logging.getLogger(__name__)
UNEXPECTED_ERROR_MESSAGE = N_("An unexpected server error occurred.")

# What an account owing a second factor may still reach: the enrolment itself,
# its own identity, and the way out.
_MFA_SETUP_ACTIONS = frozenset({
    "mfa_status", "mfa_begin", "mfa_confirm", "auth_me", "auth_logout",
    "auth_logout_all",
})


# Public routes that must read or write accounts before anybody is signed in:
# signing in, registering, recovering a password and accepting an invitation.
# They run with the system database context. Every other public route runs
# with none, so a new public route sees no protected row until it is listed
# here on purpose.
_SYSTEM_PUBLIC_ACTIONS = frozenset({
    "auth_login", "auth_mfa_login", "auth_register",
    "password_reset_request", "password_reset_complete",
    "invitations_preview", "invitations_accept",
})


def _database_scope(action, user):
    """The database context a routed handler runs in (row-level security)."""
    if user is not None:
        return user_context(user)
    if action in _SYSTEM_PUBLIC_ACTIONS:
        return system_context(f"public route {action}")
    return no_context()


def _request_id() -> str:
    """Return an opaque correlation ID that contains no request data."""
    return uuid.uuid4().hex


def _sanitized_frames(exc: Exception) -> str:
    """Project a traceback to function names and line numbers only."""
    frames = traceback.extract_tb(exc.__traceback__)
    return ",".join(f"{frame.name}:{frame.lineno}" for frame in frames)


def _log_unexpected(exc: Exception, request_id: str, method: str, route: str):
    """Log allowlisted correlation metadata without rendering the exception."""
    logger.error(
        "unexpected_error request_id=%s exception=%s method=%s route=%s frames=%s",
        request_id,
        type(exc).__name__,
        method,
        route,
        _sanitized_frames(exc),
    )



# ─── TOKEN EXTRACTION ──────────────────────────────────────────────────────────

def _extract_token(handler) -> str:
    return extract_session_cookie(handler.headers.get("Cookie", ""))


# ─── BODY PARSING ──────────────────────────────────────────────────────────────

class RequestBodyError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def _read_body(handler, max_bytes=MAX_JSON_BODY_BYTES) -> dict:
    length_text = handler.headers.get("Content-Length")
    if not isinstance(length_text, str) or not length_text.isdigit():
        raise RequestBodyError(400, t("Invalid JSON request."))
    length = int(length_text)
    if length > max_bytes:
        raise RequestBodyError(413, t("The request body is too large."))
    if length == 0:
        return {}
    # Content-Length is authoritative here. Reading one byte beyond it blocks
    # on persistent HTTP connections because the client waits for our response
    # instead of closing the socket after sending the declared body.
    raw = handler.rfile.read(length)
    if len(raw) > max_bytes:
        raise RequestBodyError(413, t("The request body is too large."))
    if len(raw) != length:
        raise RequestBodyError(400, t("Invalid JSON request."))
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RequestBodyError(400, t("Invalid JSON request."))
    if not isinstance(body, dict):
        raise RequestBodyError(400, t("Invalid JSON request."))
    return body


# Routes whose body may be larger than the default JSON limit, by action. The
# document upload carries a file of up to 10 MB as base64. server.py raises
# Flask's own limit for the same requests (body_limit_for), before the body is
# read at all.
BODY_LIMITS = {"knowledge_upload": KNOWLEDGE_MAX_BODY}


def body_limit_for(method: str, path: str, headers=None,
                   allowed_origins=DEFAULT_ALLOWED_ORIGINS) -> int:
    """The largest request body this request may send, decided before it is read.

    The larger limit of a route in BODY_LIMITS is granted only to a request
    that would get past the route's own checks: a trusted origin and a session
    whose user the route's access rule admits. Everyone else, signed out
    included, keeps the default, so nobody can make the server buffer and
    parse a large body only to be refused afterwards.
    """
    url = urlparse(path).path
    for route_method, pattern, _access, action in ROUTES:
        if route_method == method and action in BODY_LIMITS and re.match(pattern, url):
            if headers is None or not unsafe_request_is_trusted(method, headers, allowed_origins):
                return MAX_JSON_BODY_BYTES
            check = ACCESS_CHECKS[action]
            if check is not None:
                user = get_session_user(extract_session_cookie(headers.get("Cookie", "")))
                if user is None or not check(user):
                    return MAX_JSON_BODY_BYTES
            return BODY_LIMITS[action]
    return MAX_JSON_BODY_BYTES


# ─── RESPONSE HELPER ───────────────────────────────────────────────────────────

def _send(handler, status: int, data: dict):
    body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", len(body))
    for name, value in handler.__dict__.pop("_response_headers", []):
        handler.send_header(name, value)
    handler.end_headers()
    handler.wfile.write(body)


# ─── ROUTE TABLE ───────────────────────────────────────────────────────────────
# Each entry: (method, pattern, access, action)
# pattern can use named groups: (?P<id>[^/]+)
#
# access is the route's access rule, enforced by dispatch() before the handler
# runs. There is no default: a route without a valid rule stops the module
# from importing. The rule is one of
#
#   "public"         no session needed (sign-in, invitations, branding);
#   "authenticated"  any signed-in role; the handler scopes the data itself,
#                    e.g. a partner sees only their own rows;
#   a predicate name from api.authz.ROUTE_PREDICATES, e.g. "is_staff" or
#                    "can_configure"; the signed-in user must satisfy it.
#
# Handlers keep their own checks as a second line of defence. The bearer-token
# gateway (/mcp, /api/v1, /oauth) is registered separately by api.gateway and
# never reaches this table.

ROUTES = [
    # Auth (public)
    ("POST", r"^/api/auth/login$",    "public", "auth_login"),
    ("POST", r"^/api/auth/logout$",   "authenticated",  "auth_logout"),
    ("POST", r"^/api/auth/logout-all$", "authenticated", "auth_logout_all"),
    ("POST", r"^/api/auth/mfa/login$",   "public", "auth_mfa_login"),
    ("GET",  r"^/api/auth/mfa$",         "authenticated",  "mfa_status"),
    ("POST", r"^/api/auth/mfa/begin$",   "authenticated",  "mfa_begin"),
    ("POST", r"^/api/auth/mfa/confirm$", "authenticated",  "mfa_confirm"),
    ("POST", r"^/api/auth/mfa/disable$", "authenticated",  "mfa_disable"),
    ("POST", r"^/api/auth/mfa/backup-codes$", "authenticated", "mfa_backup_codes"),
    # The producer taking somebody else's factor off.
    ("POST", r"^/api/users/(?P<user_id>\d+)/mfa/reset$", "can_manage_users", "mfa_reset"),
    ("GET",  r"^/api/auth/me$",       "authenticated",  "auth_me"),
    ("POST", r"^/api/auth/register$", "public", "auth_register"),
    # Both open by design: whoever needs them cannot log in. The request
    # side answers identically for known and unknown addresses, and the
    # token pattern matches the one used for invitations.
    ("POST", r"^/api/auth/password-reset$", "public", "password_reset_request"),
    ("POST", r"^/api/auth/password-reset/(?P<token>[A-Za-z0-9_-]{16,256})$", "public", "password_reset_complete"),

    # Leads
    ("GET",   r"^/api/leads$",               "can_read_leads", "leads_list"),
    ("POST",  r"^/api/leads$",               "is_partner", "leads_create"),
    ("POST",  r"^/api/leads/precheck$",      "is_partner", "leads_precheck"),
    # Exact paths first: both must be matched before the generic
    # /api/leads/{id} patterns below them.
    ("POST",  r"^/api/leads/assign$",        "can_assign_lead", "leads_assign"),
    ("GET",   r"^/api/leads/(?P<id>[^/]+)$", "can_read_leads", "leads_get"),
    ("PATCH", r"^/api/leads/(?P<id>[^/]+)$", "is_staff", "leads_update"),
    ("PATCH", r"^/api/leads/(?P<id>[A-Z0-9-]+)/details$", "is_partner", "leads_details"),
    # The partner's own kanban move: forward/back among
    # nonterminal configured stages, terminal entry gated by an explicit
    # confirmed_terminal flag inside move_partner_lead itself.
    ("PATCH", r"^/api/leads/(?P<id>[A-Z0-9-]+)/stage$", "is_partner", "leads_move_stage"),
    # The producer's decision and the partner's answer. Named routes rather
    # than more values on the PATCH above: each carries a mandatory message,
    # a version and a replay key that a column update cannot express.
    ("POST",  r"^/api/leads/(?P<id>[A-Z0-9-]+)/registration$", "is_staff", "leads_registration"),
    ("POST",  r"^/api/leads/(?P<id>[A-Z0-9-]+)/information$",  "is_partner", "leads_information"),
    # Producer-admin-only reopen of a mistakenly won/lost lead, and the
    # settlement review that unblocks it when a commission was recorded.
    #Two named routes, not more values on
    # the stage PATCH above: each is a distinct decision with its own
    # mandatory reason and audit key, same reasoning as registration/
    # information above.
    ("POST",  r"^/api/leads/(?P<id>[A-Z0-9-]+)/reopen$",             "can_reopen_lead", "leads_reopen"),
    ("POST",  r"^/api/leads/(?P<id>[A-Z0-9-]+)/reopen-eligibility$", "can_reopen_lead", "leads_reopen_eligibility"),

    # Partners
    ("GET", r"^/api/partners$",             "is_staff", "partners_list"),
    ("GET", r"^/api/partners/(?P<id>\d+)$", "is_staff", "partners_get"),

    # Users (admin management + self-profile)
    ("GET",  r"^/api/users$",                              "can_manage_users", "users_list"),
    ("POST", r"^/api/users$",                              "can_manage_users", "users_create"),
    ("POST", r"^/api/users/invite$",                       "is_staff", "users_invite"),
    ("POST", r"^/api/users/team-invite$",                  "can_manage_users", "users_team_invite"),
    ("GET",  r"^/api/users/(?P<id>\d+)$",                 "authenticated", "users_get"),
    ("PATCH",r"^/api/users/(?P<id>\d+)$",                 "authenticated", "users_update"),
    ("POST", r"^/api/users/(?P<id>\d+)/password$",        "authenticated", "users_password"),
    # Staff accounts only, admin only, and refused when the account carries
    # financial or decision history -- see api/users.py::delete_user.
    ("DELETE", r"^/api/users/(?P<id>\d+)$",               "can_manage_users", "users_delete"),

    # Partner invitations. Public: a valid token is the
    # bearer credential here, so no session is required to preview or
    # accept one. The token pattern's length floor/ceiling rejects a
    # malformed token as a route miss rather than a database query.
    ("GET",  r"^/api/invitations/(?P<token>[A-Za-z0-9_-]{16,256})$",        "public", "invitations_preview"),
    ("POST", r"^/api/invitations/(?P<token>[A-Za-z0-9_-]{16,256})/accept$", "public", "invitations_accept"),

    # Notifications
    ("GET",    r"^/api/notifications$",               "authenticated", "notif_list"),
    ("POST",   r"^/api/notifications/read$",          "authenticated", "notif_mark_read"),
    ("DELETE", r"^/api/notifications/(?P<id>\d+)$",  "authenticated", "notif_delete"),

    # Export (CSV)
    ("GET", r"^/api/export/leads$",       "can_read_leads", "export_leads"),
    ("GET", r"^/api/export/commissions$", "is_staff_or_partner", "export_commissions"),

    # Comments
    ("GET",    r"^/api/leads/(?P<id>[A-Z0-9-]+)/comments$",      "can_read_leads", "comments_list"),
    ("POST",   r"^/api/leads/(?P<id>[A-Z0-9-]+)/comments$",      "is_staff_or_partner", "comments_create"),
    ("DELETE", r"^/api/comments/(?P<id>\d+)$",                   "authenticated", "comments_delete"),

    # Settings
    ("GET",   r"^/api/settings$", "authenticated", "settings_get"),
    ("PATCH", r"^/api/settings$", "can_configure", "settings_update"),

    # Audit log
    ("GET", r"^/api/audit$", "can_view_audit", "audit_list"),
    # Answering a person who asks what is held about them, or to be forgotten.
    ("POST", r"^/api/data-requests/export$",          "can_handle_data_requests", "gdpr_export"),
    ("POST", r"^/api/data-requests/erasure-preview$", "can_handle_data_requests", "gdpr_erasure_preview"),
    ("POST", r"^/api/data-requests/erase$",           "can_handle_data_requests", "gdpr_erase"),

    # Email test

    # Bulk leads
    ("POST", r"^/api/leads/bulk$", "is_staff", "leads_bulk"),

    # What the producer asks about a deal. Reading the schema is open to every
    # authenticated role -- a partner has to see the questions to answer them;
    # defining it is the producer's own capability.
    ("GET",   r"^/api/lead-fields$",                    "authenticated", "lead_fields_list"),
    ("POST",  r"^/api/lead-fields$",                    "can_configure_lead_fields", "lead_fields_create"),
    ("PATCH", r"^/api/lead-fields/(?P<id>\d+)$",        "can_configure_lead_fields", "lead_fields_update"),

    # Stats
    ("GET", r"^/api/stats$", "authenticated", "stats"),

    # Commission Tiers
    ("GET",    r"^/api/tiers$",            "authenticated", "tiers_list"),
    ("POST",   r"^/api/tiers$",            "can_configure", "tiers_create"),
    ("PATCH",  r"^/api/tiers/(?P<id>\d+)$", "can_configure", "tiers_update"),
    ("DELETE", r"^/api/tiers/(?P<id>\d+)$", "can_configure", "tiers_delete"),

    # Product catalogue
    # Blocks: per-producer add-ons that fit the core to its own process.
    ("GET",   r"^/api/blocks$",                                "can_configure", "blocks_list"),
    ("PATCH", r"^/api/blocks/(?P<block>[a-z][a-z0-9_]{2,47})$",     "can_configure", "blocks_update"),
    ("POST",  r"^/api/blocks/(?P<block>[a-z][a-z0-9_]{2,47})/run$", "can_configure", "blocks_run"),
    ("GET",    r"^/api/products$",            "authenticated", "products_list"),
    ("POST",   r"^/api/products$",            "can_configure", "products_create"),
    ("PATCH",  r"^/api/products/(?P<id>\d+)$", "can_configure", "products_update"),
    ("DELETE", r"^/api/products/(?P<id>\d+)$", "can_configure", "products_delete"),

    # Lead Stages
    ("GET",    r"^/api/lead-stages$",             "authenticated", "stages_list"),
    ("POST",   r"^/api/lead-stages$",             "can_configure", "stages_create"),
    ("PATCH",  r"^/api/lead-stages/(?P<id>\d+)$", "can_configure", "stages_update"),
    ("DELETE", r"^/api/lead-stages/(?P<id>\d+)$", "can_configure", "stages_delete"),

    # Bonus Definitions
    ("GET",    r"^/api/bonus-defs$",            "authenticated", "bdefs_list"),
    ("POST",   r"^/api/bonus-defs$",            "can_configure", "bdefs_create"),
    ("PATCH",  r"^/api/bonus-defs/(?P<id>\d+)$", "can_configure", "bdefs_update"),
    ("DELETE", r"^/api/bonus-defs/(?P<id>\d+)$", "can_configure", "bdefs_delete"),

    # Configurable registration fields. The public read is the
    # only unauthenticated one: the registration form is reached by people
    # who by definition have no account yet.
    ("GET",    r"^/api/registration-fields/public$", "public", "regfields_public"),

    # The producer's name and logo (api/branding.py). Reading is open: the
    # login page shows them to people who are not signed in yet.
    ("GET",    r"^/api/branding$",      "public", "branding_public"),
    ("GET",    r"^/api/branding/logo$", "public", "branding_logo_file"),
    ("PUT",    r"^/api/branding/logo$", "can_configure",  "branding_upload"),
    ("DELETE", r"^/api/branding/logo$", "can_configure",  "branding_delete"),

    # Knowledge base (api/knowledge.py): the producer's staff add, rename and
    # remove documents; every signed-in role lists and downloads them. The
    # upload is the one body allowed past 1 MB (BODY_LIMITS).
    ("GET",    r"^/api/knowledge$",                   "authenticated", "knowledge_list"),
    ("POST",   r"^/api/knowledge$",                   "is_staff",      "knowledge_upload"),
    ("PATCH",  r"^/api/knowledge/(?P<id>\d+)$",      "is_staff",      "knowledge_update"),
    ("DELETE", r"^/api/knowledge/(?P<id>\d+)$",      "is_staff",      "knowledge_delete"),
    ("GET",    r"^/api/knowledge/(?P<id>\d+)/file$", "authenticated", "knowledge_download"),
    ("GET",    r"^/api/registration-fields$",            "authenticated", "regfields_list"),
    ("POST",   r"^/api/registration-fields$",            "can_configure", "regfields_create"),
    ("PATCH",  r"^/api/registration-fields/(?P<id>\d+)$", "can_configure", "regfields_update"),
    ("DELETE", r"^/api/registration-fields/(?P<id>\d+)$", "can_configure", "regfields_delete"),

    # Partner Bonuses
    ("GET",    r"^/api/partner-bonuses$",                                "is_staff", "pb_list"),
    ("POST",   r"^/api/partner-bonuses/bulk$",                           "is_staff", "pb_bulk"),
    ("GET",    r"^/api/partner-bonuses/(?P<id>\d+)$",                    "is_staff_or_partner", "pb_get"),
    ("POST",   r"^/api/partner-bonuses/(?P<id>\d+)/assign$",             "is_staff", "pb_assign"),
    ("DELETE", r"^/api/partner-bonuses/(?P<id>\d+)/bonus/(?P<bid>\d+)$", "is_staff", "pb_remove"),
    ("POST",   r"^/api/partner-bonuses/(?P<id>\d+)/tier$",               "is_staff", "pb_tier"),

    # Commission payouts. Reads are staff; recording and reversing are
    # admin-only.
    ("GET",    r"^/api/payouts/outstanding$",                            "is_staff", "payout_outstanding"),
    # Wins claimed by a partner and awaiting the producer's agreement,
    # plus the record of what has actually been paid out.
    ("GET",    r"^/api/payouts/pending-wins$",                           "is_staff", "payout_pending_wins"),
    ("POST",   r"^/api/payouts/confirm-wins$",                           "can_pay_commission", "payout_confirm_wins"),
    ("GET",    r"^/api/payouts/evidence$",                               "is_staff", "payout_evidence"),
    ("GET",    r"^/api/payouts/partner/(?P<id>\d+)$",                    "is_staff", "payout_partner"),
    ("GET",    r"^/api/payouts/partner/(?P<id>\d+)/history$",            "is_staff", "payout_history"),
    # The partner's own side of the same ledger. Scoped to the session, so a
    # partner can only ever read his own figures (api/payouts.py).
    ("GET",    r"^/api/my-earnings$",                                    "is_staff_or_partner", "payout_my_earnings"),
    ("POST",   r"^/api/payouts$",                                        "can_pay_commission", "payout_record"),
    ("POST",   r"^/api/payouts/(?P<id>\d+)/reverse$",                    "can_pay_commission", "payout_reverse"),

    # One-off bonus awards. A cash bonus is granted and paid, never
    # assigned -- see api/bonus_awards.py.
    ("GET",    r"^/api/bonus-awards$",                                   "is_staff", "bonus_awards_list"),
    ("POST",   r"^/api/bonus-awards$",                                   "can_pay_commission", "bonus_awards_grant"),
    ("DELETE", r"^/api/bonus-awards/(?P<id>\d+)$",                       "can_pay_commission", "bonus_awards_revoke"),

    # AI Agent
    ("GET",  r"^/api/agent/status$",                                     "authenticated", "agent_status"),
    # Read back the caller's own transcript, so the docked chat can redraw
    # itself after a navigation (the app is multi-page).
    ("GET",  r"^/api/agent/history$",                                    "can_use_assistant", "agent_history"),
    ("POST", r"^/api/agent/chat$",                                       "can_use_assistant", "agent_chat"),
    ("POST", r"^/api/agent/reset$",                                      "authenticated", "agent_reset"),
    ("POST", r"^/api/agent/actions/(?P<id>[^/]+)/confirm$",              "authenticated", "agent_action_confirm"),
    ("POST", r"^/api/agent/actions/(?P<id>[^/]+)/cancel$",               "authenticated", "agent_action_cancel"),

    # OAuth consent. The session cookie is SameSite=Strict, so it is
    # not sent on the cross-site hop into /oauth/authorize; the consent page
    # therefore fetches these two same-origin, where it is. The Origin/Referer
    # check in dispatch is the CSRF guard for the decision, the MFA gate below
    # applies as everywhere, and the request id is single use.
    ("GET",  r"^/api/oauth/requests/(?P<req>[A-Za-z0-9_-]{32,64})$",          "authenticated", "oauth_request_get"),
    ("POST", r"^/api/oauth/requests/(?P<req>[A-Za-z0-9_-]{32,64})/decision$", "authenticated", "oauth_request_decision"),

    # Integrations: the session-side control of the API
    # gateway. Settings, the overview of every connection and the log are
    # admin-only; /me and the key/connection routes are every user's own,
    # scoped by the handlers. The Origin
    # check above guards the PATCH/POST/DELETE ones like every other route.
    ("GET",    r"^/api/integrations/settings$",                    "can_manage_integrations", "integrations_settings_get"),
    ("PATCH",  r"^/api/integrations/settings$",                    "can_manage_integrations", "integrations_settings_update"),
    ("GET",    r"^/api/integrations/me$",                          "authenticated", "integrations_me"),
    ("POST",   r"^/api/integrations/keys$",                        "authenticated", "integrations_key_create"),
    ("DELETE", r"^/api/integrations/keys/(?P<id>\d+)$",            "authenticated", "integrations_key_revoke"),
    ("DELETE", r"^/api/integrations/connections/(?P<id>\d+)$",     "authenticated", "integrations_connection_revoke"),
    ("GET",    r"^/api/integrations/admin/connections$",           "can_manage_integrations", "integrations_admin_connections"),
    ("GET",    r"^/api/integrations/admin/log$",                   "can_manage_integrations", "integrations_admin_log"),
    ("GET",    r"^/api/integrations/admin/usage$",                 "can_manage_integrations", "integrations_admin_usage"),

    # Affiliate programme. The shop's webhook and the /r/ redirect are not
    # here: they carry no session and are registered by api.affiliate itself.
    ("GET",    r"^/api/affiliate/settings$",                       "can_manage_integrations", "affiliate_settings"),
    ("PATCH",  r"^/api/affiliate/settings$",                       "can_manage_integrations", "affiliate_settings_update"),
    ("POST",   r"^/api/affiliate/secret$",                         "can_manage_integrations", "affiliate_secret"),
    ("POST",   r"^/api/affiliate/test-event$",                     "can_manage_integrations", "affiliate_test_event"),
    ("PUT",    r"^/api/affiliate/stripe-secret$",                  "can_manage_integrations", "affiliate_stripe_secret"),
    ("GET",    r"^/api/affiliate/me$",                             "is_partner", "affiliate_me"),
    ("POST",   r"^/api/affiliate/me/link$",                        "is_partner", "affiliate_my_link"),
    # Staff read the orders; deciding about money is the administrator's.
    ("GET",    r"^/api/affiliate/conversions$",                    "is_staff", "affiliate_conversions"),
    ("POST",   r"^/api/affiliate/conversions/approve$",            "can_pay_commission", "affiliate_approve"),
    ("POST",   r"^/api/affiliate/conversions/reject$",             "can_pay_commission", "affiliate_reject"),
    ("POST",   r"^/api/affiliate/conversions/(?P<id>\d+)/assign$",  "can_pay_commission", "affiliate_assign"),
    ("POST",   r"^/api/affiliate/conversions/(?P<id>\d+)/reverse$", "can_pay_commission", "affiliate_reverse"),
]


PUBLIC = "public"
AUTHENTICATED = "authenticated"
_FORBIDDEN_MESSAGE = N_("Access denied.")
_UNAUTHORIZED_MESSAGE = N_("Unauthorized. Please sign in.")


class RouteTableError(RuntimeError):
    """The route table is malformed; raised while the module is imported."""


def _compile_access_rules(routes):
    """Map each route's action to the callable that decides access.

    Raises RouteTableError for a route without a rule, a rule that is not
    "public", "authenticated" or a name in api.authz.ROUTE_PREDICATES, or an
    action that appears twice. Runs at import time, so a route that forgot its
    rule stops the server from starting rather than failing on a request.
    """
    checks = {}
    for entry in routes:
        if not isinstance(entry, tuple) or len(entry) != 4:
            raise RouteTableError(f"Route entry must be (method, pattern, access, action): {entry!r}")
        method, pattern, access, action = entry
        if not isinstance(access, str) or not access:
            raise RouteTableError(f"Route {method} {pattern} has no access rule.")
        if action in checks:
            raise RouteTableError(f"Action {action!r} is routed more than once.")
        if access == PUBLIC:
            checks[action] = None
        elif access == AUTHENTICATED:
            checks[action] = lambda user: True
        elif access in authz.ROUTE_PREDICATES:
            checks[action] = authz.ROUTE_PREDICATES[access]
        else:
            raise RouteTableError(f"Route {method} {pattern} has an unknown access rule {access!r}.")
    return checks


# action -> None for public routes, otherwise a callable taking the session user.
ACCESS_CHECKS = _compile_access_rules(ROUTES)


def dispatch(handler, method: str, path: str):
    """Route one request; every message it produces is in the caller's language."""
    with use_language(language_from_header(handler.headers.get("Accept-Language"))):
        return _dispatch(handler, method, path)


def _dispatch(handler, method: str, path: str):
    parsed = urlparse(path)
    url    = parsed.path
    qs     = parse_qs(parsed.query)
    allowed_origins = handler.__dict__.get(
        "allowed_origins", DEFAULT_ALLOWED_ORIGINS
    )

    for route_method, pattern, _access, action in ROUTES:
        if route_method != method:
            continue
        m = re.match(pattern, url)
        if not m:
            continue

        if not unsafe_request_is_trusted(method, handler.headers, allowed_origins):
            return _send(
                handler, 403, {"error": t("Request origin not allowed.")}
            )

        if method in ("POST", "PUT", "PATCH"):
            try:
                body = _read_body(handler, BODY_LIMITS.get(action, MAX_JSON_BODY_BYTES))
            except RequestBodyError as exc:
                return _send(handler, exc.status, {"error": exc.message})
        else:
            body = {}

        # Auth check
        check = ACCESS_CHECKS[action]
        needs_auth = check is not None
        token = _extract_token(handler)
        user  = get_session_user(token) if needs_auth else None

        if needs_auth and user is None:
            return _send(handler, 401, {"error": t(_UNAUTHORIZED_MESSAGE)})

        # A role that must hold a second factor gets no further than setting one
        # up. Anything softer would make "required" a label rather than a rule,
        # but the enrolment endpoints themselves stay open or the account could
        # never comply.
        if (user and mfa_is_required_for(user["role"])
                and action not in _MFA_SETUP_ACTIONS
                and not mfa_is_enabled(user["id"])):
            return _send(handler, 403, {
                "error": t("Turn on the second sign-in factor first."),
                "code": "mfa_setup_required",
            })

        # The route's own access rule, decided before the handler runs.
        if needs_auth and not check(user):
            return _send(handler, 403, {"error": t(_FORBIDDEN_MESSAGE)})

        # Dispatch
        groups = m.groupdict()

        # Every statement the handler runs carries who it acts for; the
        # database policies (migration 0016) decide what that person sees.
        with _database_scope(action, user):
            try:
                if action == "auth_login":
                    status, data = handle_login(handler, body)

                elif action == "password_reset_request":
                    status, data = password_reset_request(handler, body)

                elif action == "password_reset_complete":
                    status, data = password_reset_complete(handler, groups["token"], body)

                elif action == "auth_mfa_login":
                    status, data = handle_mfa_login(handler, body)

                elif action == "mfa_status":
                    status, data = mfa_status(handler, user)

                elif action == "mfa_begin":
                    status, data = mfa_begin(handler, user)

                elif action == "mfa_confirm":
                    status, data = mfa_confirm(handler, user, body)

                elif action == "mfa_disable":
                    status, data = mfa_disable(handler, user, body)

                elif action == "mfa_backup_codes":
                    status, data = mfa_backup_codes(handler, user, body)

                elif action == "mfa_reset":
                    status, data = mfa_reset(handler, user, groups["user_id"])

                elif action == "auth_logout":
                    status, data = handle_logout(handler, token, user)

                elif action == "auth_logout_all":
                    status, data = handle_logout_everywhere(handler, user)

                elif action == "auth_me":
                    status, data = handle_me(handler, user)

                elif action == "auth_register":
                    status, data = users_register(handler, body)

                elif action == "leads_list":
                    status, data = leads_list(handler, user, qs)

                elif action == "leads_create":
                    status, data = leads_create(handler, user, body)

                elif action == "leads_assign":
                    status, data = leads_assign(handler, user, body)

                elif action == "leads_precheck":
                    status, data = leads_precheck(handler, user, body)

                elif action == "leads_get":
                    status, data = leads_get(handler, user, groups["id"])

                elif action == "leads_update":
                    status, data = leads_update(handler, user, groups["id"], body)

                elif action == "leads_details":
                    status, data = leads_details(handler, user, groups["id"], body)

                elif action == "leads_move_stage":
                    status, data = leads_move_stage(handler, user, groups["id"], body)

                elif action == "leads_registration":
                    status, data = leads_registration(handler, user, groups["id"], body)

                elif action == "leads_information":
                    status, data = leads_information(handler, user, groups["id"], body)

                elif action == "leads_reopen":
                    status, data = leads_reopen(handler, user, groups["id"], body)

                elif action == "leads_reopen_eligibility":
                    status, data = leads_reopen_eligibility(handler, user, groups["id"], body)

                elif action == "partners_list":
                    status, data = partners_list(handler, user)

                elif action == "partners_get":
                    status, data = partners_get(handler, user, int(groups["id"]))

                elif action == "users_list":
                    status, data = users_list(handler, user, qs)

                elif action == "users_create":
                    status, data = users_create(handler, user, body)

                elif action == "users_get":
                    status, data = users_get(handler, user, int(groups["id"]))

                elif action == "users_update":
                    status, data = users_update(handler, user, int(groups["id"]), body)

                elif action == "users_password":
                    status, data = users_password(handler, user, int(groups["id"]), body)
                elif action == "users_delete":
                    status, data = users_delete(handler, user, int(groups["id"]))

                elif action == "users_invite":
                    status, data = invitations_create(handler, user, body)

                # Team invitation: team-invite is its own
                # route and its own handler, separate from the partner path above.
                elif action == "users_team_invite":
                    status, data = invitations_create_team(handler, user, body)

                elif action == "invitations_preview":
                    status, data = invitations_preview(handler, groups["token"])

                elif action == "invitations_accept":
                    status, data = invitations_accept(handler, groups["token"], body)

                elif action == "notif_list":
                    status, data = notif_list(handler, user, qs)

                elif action == "notif_mark_read":
                    status, data = notif_mark_read(handler, user, qs, body)

                elif action == "notif_delete":
                    status, data = notif_delete(handler, user, int(groups["id"]))

                elif action == "export_leads":
                    status, data = handle_export_leads(handler, user, qs)
                    if status is None:   # already sent raw CSV
                        return True

                elif action == "export_commissions":
                    status, data = handle_export_commissions(handler, user, qs)
                    if status is None:
                        return True

                elif action == "stats":
                    status, data = handle_stats(handler, user)

                elif action == "comments_list":
                    status, data = comments_list(handler, user, groups["id"])

                elif action == "comments_create":
                    status, data = comments_create(handler, user, groups["id"], body)

                elif action == "comments_delete":
                    status, data = comments_delete(handler, user, int(groups["id"]))

                elif action == "settings_get":
                    status, data = settings_get(handler, user, qs)

                elif action == "gdpr_export":
                    status, data = gdpr_export(handler, user, body)

                elif action == "gdpr_erasure_preview":
                    status, data = gdpr_erasure_preview(handler, user, body)

                elif action == "gdpr_erase":
                    status, data = gdpr_erase(handler, user, body)

                elif action == "settings_update":
                    status, data = settings_update(handler, user, body)

                elif action == "audit_list":
                    status, data = audit_list(handler, user, qs)

                elif action == "lead_fields_list":
                    status, data = lead_fields_list(handler, user, qs)

                elif action == "lead_fields_create":
                    status, data = lead_fields_create(handler, user, body)

                elif action == "lead_fields_update":
                    status, data = lead_fields_update(handler, user, groups["id"], body)

                elif action == "leads_bulk":
                    status, data = leads_bulk(handler, user, body)

                # ── Tiers ──────────────────────────────────────────────
                elif action == "tiers_list":
                    status, data = tiers_list(handler, user, qs)
                elif action == "tiers_create":
                    status, data = tiers_create(handler, user, body)
                elif action == "tiers_update":
                    status, data = tiers_update(handler, user, groups["id"], body)
                elif action == "tiers_delete":
                    status, data = tiers_delete(handler, user, groups["id"])

                # ── Product catalogue ──────────────────────────────────────────
                elif action == "blocks_list":
                    status, data = blocks_list(handler, user, qs)
                elif action == "blocks_update":
                    status, data = blocks_update(handler, user, groups["block"], body)
                elif action == "blocks_run":
                    status, data = blocks_run(handler, user, groups["block"])
                elif action == "products_list":
                    status, data = products_list(handler, user, qs)
                elif action == "products_create":
                    status, data = products_create(handler, user, body)
                elif action == "products_update":
                    status, data = products_update(handler, user, groups["id"], body)
                elif action == "products_delete":
                    status, data = products_delete(handler, user, groups["id"])

                # ── Lead Stages ─────────────────────────────────
                elif action == "stages_list":
                    status, data = stages_list(handler, user, qs)
                elif action == "stages_create":
                    status, data = stages_create(handler, user, body)
                elif action == "stages_update":
                    status, data = stages_update(handler, user, groups["id"], body)
                elif action == "stages_delete":
                    status, data = stages_delete(handler, user, groups["id"])

                # ── Bonus Definitions ───────────────────────────────────
                elif action == "bdefs_list":
                    status, data = bdefs_list(handler, user, qs)
                elif action == "bdefs_create":
                    status, data = bdefs_create(handler, user, body)
                elif action == "bdefs_update":
                    status, data = bdefs_update(handler, user, groups["id"], body)
                elif action == "bdefs_delete":
                    status, data = bdefs_delete(handler, user, groups["id"])
                elif action == "regfields_public":
                    status, data = regfields_public(handler, qs)

                elif action == "branding_public":
                    status, data = branding_public(handler)

                elif action == "branding_logo_file":
                    status, data = branding_logo_file(handler)
                    if status is None:   # already sent the image
                        return True

                elif action == "branding_upload":
                    status, data = branding_upload(handler, user, body)

                elif action == "branding_delete":
                    status, data = branding_delete(handler, user)

                elif action == "knowledge_list":
                    status, data = knowledge_list(handler, user)
                elif action == "knowledge_upload":
                    status, data = knowledge_upload(handler, user, body)
                elif action == "knowledge_update":
                    status, data = knowledge_update(handler, user, groups["id"], body)
                elif action == "knowledge_delete":
                    status, data = knowledge_delete(handler, user, groups["id"])
                elif action == "knowledge_download":
                    status, data = knowledge_download(handler, user, groups["id"])
                    if status is None:   # already sent the file
                        return True
                elif action == "regfields_list":
                    status, data = regfields_list(handler, user, qs)
                elif action == "regfields_create":
                    status, data = regfields_create(handler, user, body)
                elif action == "regfields_update":
                    status, data = regfields_update(handler, user, groups["id"], body)
                elif action == "regfields_delete":
                    status, data = regfields_delete(handler, user, groups["id"])

                # ── Partner Bonuses ─────────────────────────────────────
                elif action == "pb_list":
                    status, data = pb_list(handler, user, qs)
                elif action == "pb_bulk":
                    status, data = pb_bulk(handler, user, body)
                elif action == "pb_get":
                    status, data = pb_get(handler, user, groups["id"])
                elif action == "bonus_awards_list":
                    status, data = bonus_awards_list(handler, user, qs)
                elif action == "bonus_awards_grant":
                    status, data = bonus_awards_grant(handler, user, body)
                elif action == "bonus_awards_revoke":
                    status, data = bonus_awards_revoke(handler, user, groups["id"])
                elif action == "pb_assign":
                    status, data = pb_assign(handler, user, groups["id"], body)
                elif action == "pb_remove":
                    status, data = pb_remove(handler, user, groups["id"], groups["bid"])
                elif action == "pb_tier":
                    status, data = pb_tier(handler, user, groups["id"], body)

                # ── Commission payouts ───────────────────────────
                elif action == "payout_outstanding":
                    status, data = payout_outstanding(handler, user)
                elif action == "payout_pending_wins":
                    status, data = payout_pending_wins(handler, user)
                elif action == "payout_confirm_wins":
                    status, data = payout_confirm_wins(handler, user, body)
                elif action == "payout_evidence":
                    status, data = payout_evidence(handler, user, qs)
                elif action == "payout_partner":
                    status, data = payout_partner(handler, user, groups["id"])
                elif action == "payout_history":
                    status, data = payout_history(handler, user, groups["id"])
                elif action == "payout_my_earnings":
                    status, data = payout_my_earnings(handler, user, qs)
                elif action == "payout_record":
                    status, data = payout_record(handler, user, body)
                elif action == "payout_reverse":
                    status, data = payout_reverse(handler, user, groups["id"], body)

                elif action == "agent_history":
                    status, data = agent_history(handler, user)
                elif action == "agent_status":
                    status, data = agent_status(handler, user)
                elif action == "agent_chat":
                    status, data = agent_chat(handler, user, body)
                elif action == "agent_reset":
                    status, data = agent_reset(handler, user, body)
                elif action == "agent_action_confirm":
                    status, data = agent_action_confirm(handler, user, groups["id"])
                elif action == "agent_action_cancel":
                    status, data = agent_action_cancel(handler, user, groups["id"])

                elif action == "oauth_request_get":
                    status, data = oauth_request_get(handler, user, groups["req"])
                elif action == "oauth_request_decision":
                    status, data = oauth_request_decision(handler, user, groups["req"], body)

                elif action == "integrations_settings_get":
                    status, data = integrations_settings_get(handler, user)
                elif action == "integrations_settings_update":
                    status, data = integrations_settings_update(handler, user, body)
                elif action == "integrations_me":
                    status, data = integrations_me(handler, user)
                elif action == "integrations_key_create":
                    status, data = integrations_key_create(handler, user, body)
                elif action == "integrations_key_revoke":
                    status, data = integrations_key_revoke(handler, user, groups["id"])
                elif action == "integrations_connection_revoke":
                    status, data = integrations_connection_revoke(handler, user, groups["id"])
                elif action == "integrations_admin_connections":
                    status, data = integrations_admin_connections(handler, user)
                elif action == "integrations_admin_log":
                    status, data = integrations_admin_log(handler, user, qs)
                elif action == "integrations_admin_usage":
                    status, data = integrations_admin_usage(handler, user, qs)

                elif action.startswith("affiliate_"):
                    status, data = affiliate_route(action, user, groups, qs, body)

                else:
                    status, data = 404, {"error": t("Not found")}

            except ValidationError as exc:
                # A rule the caller can fix. Its message is written by us, never
                # built from the request, so it is safe to return verbatim -- and
                # far more useful than a generic 500 with a request id.
                status, data = 400, {"error": str(exc)}

            except Exception as exc:
                request_id = _request_id()
                _log_unexpected(exc, request_id, method, action)
                status, data = 500, {
                    "error": t(UNEXPECTED_ERROR_MESSAGE),
                    "request_id": request_id,
                }

        return _send(handler, status, data)

    # No route matched
    return None   # Let the caller serve static files or return 404
