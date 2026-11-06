"""
PartnerType — Leads Module
CRUD for leads, status management, commission setting.
"""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
import logging
import hashlib
import json
import re
import traceback
import uuid

from api.authz import (
    can_assign_lead, can_pay_commission, can_read_leads, can_reopen_lead, is_staff,
)
from api.db import (
    get_conn,
    next_lead_id,
    lead_workflow_transaction,
    LeadNotFoundError,
    StaleRevisionError,
    OperationConflictError,
)
from api import notifications as notif
from api import audit
from api import email_service as mailer
from api import lead_stages
from api import lead_fields
from api import partner_bonuses
from api import blocks
from api.postgres import system_context
from api.settings_api import get as get_setting
from api.validation import (
    parse_allowed_fields,
    parse_boolean_flag,
    parse_email,
    parse_enum,
    parse_finite_number,
    parse_integer_id,
    parse_nip,
    parse_non_negative_integer,
    parse_optional_string,
    parse_required_string,
    parse_website,
)
from api.i18n import N_, t

logger = logging.getLogger(__name__)

_LEAD_CREATE_FIELDS = (
    "client_name", "client_email", "client_phone", "company", "value", "notes", "sector",
    "nip", "website", "product", "product_id", "quantity",
    # What the producer additionally asks about this deal, and the version of
    # the question set the partner actually saw.
    "lead_field_values", "lead_fields_version",
)
_MAX_LEAD_QUANTITY = 1_000_000
# The producer's assignment envelope: the partner's own form, plus who owns
# the result, which version of the form it was drawn from, the explicit
# confirmation of acceptance and the replay key. Deliberately excludes
# lead_fields_version -- the assignment form sends schema_revision, the name
# every other lead endpoint uses, and the adapter maps it onto the shared
# validator rather than the partner contract growing a second spelling.
_LEAD_ASSIGN_FIELDS = tuple(
    name for name in _LEAD_CREATE_FIELDS if name != "lead_fields_version"
) + ("partner_id", "schema_revision", "confirm_accept", "operation_id")
_LEAD_UPDATE_FIELDS = (
    "status", "registration_status", "commission", "commission_override_pct",
    "registration_message",
)
_LEAD_FILTER_FIELDS = (
    "status", "registration_status", "partner_id", "q", "date_from", "date_to",
    "min_value", "overdue", "won_from", "won_to",
)

# A lead still awaiting the producer's decision past the deadline they set
# themselves. Written once, in SQL, because three screens ask the question and
# must agree about the same lead. The `?` takes `settings_api.action_days()`.
# Also read by api/stats.py.
OVERDUE_LEAD_CONDITION = (
    "l.registration_status IN ('pending', 'needs_info')"
    " AND EXTRACT(EPOCH FROM (timezone('UTC', now()) - l.created_at::timestamp))/86400 > ?"
)

# Sort keys the list accepts, mapped to the expressions that order them.
# A whitelist rather than interpolation of whatever arrives: the sort key lands
# in the SQL text itself, where a bound parameter cannot protect it.
#
# `commission` sorts on the amount actually recorded, with the order value as
# the tiebreak. The column shows the recorded commission where there is one and
# the forecast everywhere else, and the forecast is a percentage of the order
# value -- so ordering the not-yet-won leads by value puts them in the order
# their forecasts are in, for every partner on the same tier. Sorting them
# properly would mean recomputing every tier and bonus in SQL.
_LEAD_SORT_COLUMNS = {
    "created_at":          ("l.created_at",),
    "client_name":         ("LOWER(l.client_name)",),
    "company":             ("LOWER(l.company)",),
    "partner_company":     ("LOWER(u.company)",),
    "value":               ("l.value",),
    "commission":          ("COALESCE(l.commission, 0)", "l.value"),
    "registration_status": ("l.registration_status",),
    "status":              ("l.status",),
}

# Every ordering ends here. Without a tiebreak on a unique column, two leads
# holding the same value may swap places between two queries, and a row that
# swaps across a page boundary is shown twice or not at all -- the classic way
# paging quietly loses records.
_LEAD_SORT_TIEBREAK = "l.id DESC"

# The producer's default view: everything still waiting on them first, oldest
# of those at the top. The queue they have to work, not an alphabet. It ignores
# the sort direction, as it did in the browser -- "least urgent first" is not a
# view anyone asked for.
_LEAD_SORT_ATTENTION = (
    "CASE WHEN l.registration_status IN ('pending', 'needs_info') THEN 0 ELSE 1 END ASC,"
    " l.created_at ASC"
)

LEADS_PAGE_SIZE = 25
LEADS_MAX_PAGE_SIZE = 100

# The four fixed values of the registration axis. Unlike
# the sales pipeline below them, these are NOT producer-configurable ---
# they are the decision the producer makes about a submitted deal, and
# every one of them has behaviour attached (a notification, an email, an
# audit key), so a producer renaming or deleting one would break the
# process rather than customise it.
REGISTRATION_STATUSES = ("pending", "needs_info", "accepted", "rejected")
REGISTRATION_LABELS = {
    "pending":    N_("Pending"),
    "needs_info": N_("Information requested"),
    "accepted":   N_("Accepted"),
    "rejected":   N_("Rejected"),
}
_MAX_MONEY = 1_000_000_000_000
_LEAD_ID_RE = re.compile(r"^LD-[A-Z0-9-]+$")

# Client-identity fields required on the partner submission boundary.
# Enforced by the authenticated submit/preview boundary rather than
# create_lead, since create_lead is also called by seed scripts and unit
# fixtures that need not supply them.
_LEAD_IDENTITY_REQUIRED_FIELDS = (
    ("nip", N_("NIP")),
    ("website", N_("Website")),
    ("product_id", N_("Product")),
    ("quantity", N_("Quantity")),
)


def _parse_lead_id(value):
    normalized = parse_required_string(value, N_("Lead ID"), max_length=32)
    if not _LEAD_ID_RE.fullmatch(normalized):
        raise ValueError(t("Invalid lead id."))
    return normalized


def _parse_date_filter(value, field):
    normalized = parse_optional_string(value, field, max_length=10)
    if not normalized:
        return ""
    try:
        date.fromisoformat(normalized)
    except ValueError:
        raise ValueError(t("Field '{field}' must be a date in the YYYY-MM-DD format.", field=field))
    return normalized


# "Today" for the vendor is a Polish calendar day. won_at is stored as UTC
# text (datetime.utcnow().isoformat()), so a Warsaw day is turned into a UTC
# [start, end) range before it reaches SQL. The DST
# change days are 23 and 25 hours long; zoneinfo handles both.
BUSINESS_TZ = ZoneInfo("Europe/Warsaw")


def warsaw_day_bounds_utc(day_iso):
    """(start, end) of the Europe/Warsaw day `day_iso` (YYYY-MM-DD) as naive
    UTC ISO text in the format won_at is written in, end exclusive."""
    if not isinstance(day_iso, str):
        raise ValueError(t("The day must be a date in the YYYY-MM-DD format."))
    try:
        day = date.fromisoformat(day_iso)
    except ValueError:
        raise ValueError(t("The day must be a date in the YYYY-MM-DD format.")) from None
    if len(day_iso) != 10:
        raise ValueError(t("The day must be a date in the YYYY-MM-DD format."))

    def to_utc(local_day):
        local = datetime.combine(local_day, time(0, 0), tzinfo=BUSINESS_TZ)
        return local.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")

    return to_utc(day), to_utc(day + timedelta(days=1))


def warsaw_today():
    """Today's date in Europe/Warsaw, YYYY-MM-DD."""
    return datetime.now(BUSINESS_TZ).date().isoformat()


def _normalize_optional_identity(value, parser):
    """Normalize a client-identity value through ``parser`` when it is a
    non-empty string; otherwise store the empty string (no identity claim).
    """
    if isinstance(value, str) and value.strip():
        return parser(value)
    return ""


def _validate_lead_create(data):
    values = parse_allowed_fields(data, _LEAD_CREATE_FIELDS, N_("Lead data"))
    quantity = values.get("quantity")
    return {
        "client_name": parse_required_string(
            values.get("client_name"), N_("Client full name"), max_length=200
        ),
        "client_email": parse_email(values.get("client_email"), N_("Client e-mail")),
        "client_phone": parse_optional_string(
            values.get("client_phone"), N_("Client phone"), max_length=50
        ),
        "company": parse_required_string(
            values.get("company"), N_("Client company"), max_length=200
        ),
        "value": parse_finite_number(
            values.get("value"), N_("Value"), minimum=0, maximum=_MAX_MONEY
        ),
        "notes": parse_optional_string(values.get("notes"), N_("Notes"), max_length=5000),
        "sector": parse_optional_string(values.get("sector"), N_("Sector"), max_length=100),
        "nip": _normalize_optional_identity(values.get("nip"), parse_nip),
        "website": _normalize_optional_identity(values.get("website"), parse_website),
        "product": parse_optional_string(values.get("product"), "product", max_length=200),
        "product_id": (
            parse_integer_id(values["product_id"], N_("Product"))
            if values.get("product_id") is not None else None
        ),
        "quantity": (
            parse_non_negative_integer(quantity, N_("Quantity"), maximum=_MAX_LEAD_QUANTITY)
            if quantity is not None else 0
        ),
        # Passed through untouched; lead_fields owns their validation, because
        # what is valid depends on definitions this module does not know.
        "lead_field_values": values.get("lead_field_values"),
        "lead_fields_version": values.get("lead_fields_version"),
    }


def _validate_lead_update(data):
    values = parse_allowed_fields(data, _LEAD_UPDATE_FIELDS, N_("Update data"))
    normalized = {}
    if "status" in values:
        # Validated against the active stage list at call time (not a
        # module constant captured at import) -- a producer can add a
        # stage while the process is running.
        active_keys = tuple(lead_stages.get_active_stage_keys())
        normalized["status"] = parse_enum(values["status"], "status", active_keys)
    if "registration_status" in values:
        normalized["registration_status"] = parse_enum(
            values["registration_status"], "registration_status", REGISTRATION_STATUSES
        )
        # Enforced in the domain so EVERY writer obeys these two rules -- the
        # AI adapter and the demo seed included, not just HTTP. Leaving them at
        # the boundary would make the generic update a bypass.
        if normalized["registration_status"] == "pending":
            raise ValueError(
                t("A submission cannot be moved back to pending.")
            )
        if (normalized["registration_status"] in ("needs_info", "rejected")
                and not str(values.get("registration_message") or "").strip()):
            raise ValueError(
                t("A rejection or a request for information needs a message for the partner.")
            )
    if "registration_message" in values:
        if normalized.get("registration_status") not in ("needs_info", "rejected"):
            raise ValueError(t("A decision message needs a request for information or a "
                               "rejection."))
        normalized["registration_message"] = parse_required_string(
            values["registration_message"], N_("Decision message"), max_length=4000,
        )
    if "commission" in values and values["commission"] is not None:
        normalized["commission"] = parse_finite_number(
            values["commission"], "commission", minimum=0, maximum=_MAX_MONEY
        )
    if "commission_override_pct" in values:
        # An explicit null clears the correction and hands the deal back to
        # the partner's own tier rate. 100% is the ceiling: a commission
        # larger than the contract is a typo, not a decision.
        raw = values["commission_override_pct"]
        normalized["commission_override_pct"] = (
            None if raw is None
            else parse_finite_number(
                raw, "commission_override_pct", minimum=0, maximum=100
            )
        )
    if not normalized:
        raise ValueError(t("No data to update."))
    return normalized


def _validate_lead_filters(filters):
    values = parse_allowed_fields(filters or {}, _LEAD_FILTER_FIELDS, N_("Lead filters"))
    normalized = {}
    if values.get("status") is not None:
        active_keys = tuple(lead_stages.get_active_stage_keys())
        normalized["status"] = parse_enum(values["status"], "status", active_keys)
    if values.get("registration_status") is not None:
        normalized["registration_status"] = parse_enum(
            values["registration_status"], "registration_status", REGISTRATION_STATUSES
        )
    if values.get("partner_id") is not None:
        normalized["partner_id"] = parse_integer_id(values["partner_id"], "partner_id")
    if values.get("q") is not None:
        normalized["q"] = parse_optional_string(values["q"], "q", max_length=200)
    if values.get("date_from") is not None:
        normalized["date_from"] = _parse_date_filter(values["date_from"], "date_from")
    if values.get("date_to") is not None:
        normalized["date_to"] = _parse_date_filter(values["date_to"], "date_to")
    if values.get("min_value") is not None:
        normalized["min_value"] = parse_finite_number(
            values["min_value"], "min_value", minimum=0, maximum=_MAX_MONEY
        )
    if values.get("overdue") is not None:
        normalized["overdue"] = _parse_filter_flag(values["overdue"], "overdue")
    for field in ("won_from", "won_to"):
        if values.get(field) is not None:
            normalized[field] = _parse_date_filter(values[field], field)
    if (normalized.get("won_from") and normalized.get("won_to")
            and normalized["won_to"] < normalized["won_from"]):
        raise ValueError(t("Field 'won_to' cannot be earlier than 'won_from'."))
    return normalized


def _parse_filter_flag(value, field):
    """Return a bool for a filter that arrives as a query string or a real bool.

    The same filters travel two ways: as `?overdue=1` from the browser and as
    Python values from the AI adapter and the tests. parse_boolean_flag refuses
    strings on purpose (a write must not accept "false" as true); a filter is
    the one place where the string form is the normal one, so the accepted
    spellings are listed here rather than loosening the shared parser.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("1", "true", "tak"):
            return True
        if normalized in ("", "0", "false", "nie"):
            return False
    raise ValueError(t("Field '{field}' must be a boolean.", field=t(field)))


def _parse_lead_sort(sort, direction):
    """Return the ORDER BY clause for a whitelisted sort key, or raise.

    No sort means newest first, which is what this list has always returned and
    what several callers quietly depend on: the dashboards take the first five
    rows and label them "ostatnie". The producer's queue view has to ask for
    `attention` by name rather than inherit it.
    """
    if sort in (None, ""):
        return f"l.created_at DESC, {_LEAD_SORT_TIEBREAK}"

    key = str(sort).strip()
    descending = str(direction or "asc").strip().lower() == "desc"

    if key == "attention":
        return f"{_LEAD_SORT_ATTENTION}, {_LEAD_SORT_TIEBREAK}"
    columns = _LEAD_SORT_COLUMNS.get(key)
    if columns is None:
        raise ValueError(t("Field '{field}' has an invalid value: {value}", field="sort", value=key))
    order = "DESC" if descending else "ASC"
    return ", ".join([*(f"{column} {order}" for column in columns), _LEAD_SORT_TIEBREAK])


def _require_partner_submission(user, data):
    """Enforce the authenticated lead-submission boundary for every adapter.

    HTTP and AI both call this helper so a new required submission field cannot
    be accidentally enforced by one interface and skipped by the other.
    """
    if user["role"] != "partner":
        raise PermissionError(t("Only a partner can submit leads."))
    if not isinstance(data, dict):
        raise ValueError(t("Invalid lead data."))
    for field, label in _LEAD_IDENTITY_REQUIRED_FIELDS:
        value = data.get(field)
        # 0 is a value, not a missing field: say what is wrong with it.
        if field == "quantity" and value is not None and value != "" and str(value).strip() in ("0", "0.0"):
            raise ValueError(t("Field 'Quantity' must be at least 1."))
        if not value:
            raise ValueError(t("Field '{field}' is required.", field=t(label)))


def _resolve_product_and_queue(conn, data, partner_id=None):
    """Resolve catalogue and duplicate-queue facts shared by preview/create."""
    resolved = dict(data)
    if resolved["product_id"] is not None:
        product_row = conn.execute(
            "SELECT name FROM products WHERE id=? AND active=1 AND offered=1",
            (resolved["product_id"],),
        ).fetchone()
        if product_row is None:
            raise ValueError(t("The selected product does not exist or is not offered to "
                               "partners."))
        resolved["product"] = product_row["name"]

    duplicates = find_client_duplicates(conn, resolved["nip"], resolved["website"])
    _refuse_own_duplicate(duplicates, partner_id)
    duplicate_policy = get_setting("lead_duplicate_policy") or "queue"
    if duplicates and duplicate_policy == "reject":
        raise ValueError(
            t("This client has already been submitted (same NIP or domain) and the vendor "
              "does not accept repeat submissions.")
        )
    if duplicate_policy == "allow":
        queue_position = 1
    else:
        queue_max = int(get_setting("lead_queue_max") or 5)
        queue_position = min(len(duplicates) + 1, queue_max)

    queue_shares = [
        float(part.strip())
        for part in (get_setting("lead_queue_shares") or "100").split(",")
    ]
    queue_index = min(queue_position, len(queue_shares)) - 1
    return resolved, {
        "duplicate": bool(duplicates),
        "policy": duplicate_policy,
        "queue_position": queue_position,
        "queue_share": queue_shares[queue_index],
    }


def _unexpected_bulk_item(item_id, exc):
    request_id = uuid.uuid4().hex
    frames = traceback.extract_tb(exc.__traceback__)
    frame_projection = ",".join(
        f"{frame.name}:{frame.lineno}" for frame in frames
    )
    logger.error(
        "unexpected_error request_id=%s exception=%s method=POST "
        "route=leads_bulk frames=%s",
        request_id,
        type(exc).__name__,
        frame_projection,
    )
    return {"id": item_id, "code": "unexpected_error", "request_id": request_id}



# Columns the text search `q` may look in. The app searches all of them; the
# API gateway narrows the list to the field groups the administrator shares,
#so a search cannot reveal a hidden contact name or sector.
LEAD_SEARCH_COLUMNS = ("client_name", "company", "id", "sector")


def _lead_search_columns(search_columns):
    if search_columns is None:
        return LEAD_SEARCH_COLUMNS
    columns = tuple(c for c in LEAD_SEARCH_COLUMNS if c in set(search_columns))
    if not columns:
        raise ValueError("search_columns must name at least one searchable column")
    return columns


def _lead_list_conditions(user, filters, search_columns=None):
    """Return (where_sql, params) for a validated filter set.

    Shared by the unbounded read and the paged one so a page and its total can
    never be counted against different conditions -- the bug that shows "1-25
    of 300" above a list that ends at row 4. `search_columns` limits which
    LEAD_SEARCH_COLUMNS the text search `q` looks in (default: all of them);
    only allowlisted names ever reach the SQL.
    """
    params = []
    where  = []

    if user["role"] == "partner":
        where.append("l.partner_id = ?")
        params.append(user["id"])
    elif filters.get("partner_id"):
        where.append("l.partner_id = ?")
        params.append(filters["partner_id"])

    if filters.get("status"):
        where.append("l.status = ?")
        params.append(filters["status"])

    if filters.get("registration_status"):
        where.append("l.registration_status = ?")
        params.append(filters["registration_status"])

    if filters.get("q"):
        q = f"%{filters['q']}%"
        columns = _lead_search_columns(search_columns)
        where.append("(" + " OR ".join(f"l.{column} ILIKE ?" for column in columns) + ")")
        params.extend([q] * len(columns))

    if filters.get("date_from"):
        where.append("l.created_at >= ?")
        params.append(filters["date_from"])

    if filters.get("date_to"):
        where.append("l.created_at <= ?")
        params.append(filters["date_to"] + "T23:59:59")

    if filters.get("min_value") is not None:
        where.append("COALESCE(l.value, 0) >= ?")
        params.append(filters["min_value"])

    # Day of the WIN, in Europe/Warsaw days. replace() makes an imported
    # 'YYYY-MM-DD HH:MM:SS' value compare as the same instant as the
    # 'YYYY-MM-DDTHH:MM:SS' the app writes; a lead never won (NULL) never matches.
    if filters.get("won_from"):
        where.append("l.won_at IS NOT NULL AND replace(l.won_at, ' ', 'T') >= ?")
        params.append(warsaw_day_bounds_utc(filters["won_from"])[0])

    if filters.get("won_to"):
        where.append("l.won_at IS NOT NULL AND replace(l.won_at, ' ', 'T') < ?")
        params.append(warsaw_day_bounds_utc(filters["won_to"])[1])

    if filters.get("overdue"):
        from api import settings_api  # imported here to avoid a circular import
        where.append(f"({OVERDUE_LEAD_CONDITION})")
        params.append(settings_api.action_days())

    return (" WHERE " + " AND ".join(where) if where else ""), params


_LEAD_LIST_FROM = """
    FROM leads l
    JOIN users u ON u.id = l.partner_id
    LEFT JOIN products p ON p.id = l.product_id
"""
# product_en is the CATALOGUE'S CURRENT English name for whatever product_id
# this lead points at -- a live projection, not a snapshot like
# bonus_awards.name_en. leads.product itself (the Polish text
# recorded at submission) is never rewritten by this join; a lead whose
# product_id is NULL (predates the catalogue) or whose product has no EN
# name yet gets '' here, and every reader falls back to the persisted
# Polish leads.product through Ui.producerLabel.
_LEAD_LIST_SELECT = f"""
    SELECT l.*, u.name AS partner_name, u.company AS partner_company,
           COALESCE(p.name_en, '') AS product_en
    {_LEAD_LIST_FROM}
"""


def get_leads(user: dict, filters: dict = None, sort: str = None, direction: str = None):
    """
    Return leads list. Partners see only their own.

    Filters: registration_status (the producer's decision), status (the
    sales-pipeline stage), partner_id, search (q), date range, minimum order
    value, and whether the producer's own decision deadline has passed. The two
    status filters are independent -- "every accepted deal" and "every deal at
    the negotiation stage" are different questions.

    This returns EVERY match, deliberately. Callers that compute a total over a
    partner's whole history depend on that: api/payouts.py's pipeline forecast
    would silently under-report against a truncated list. The HTTP list asks for
    a page instead, through get_leads_page().
    """
    filters = _validate_lead_filters(filters)
    order_by = _parse_lead_sort(sort, direction)
    conn = get_conn()
    try:
        where_sql, params = _lead_list_conditions(user, filters)
        rows = conn.execute(
            f"{_LEAD_LIST_SELECT}{where_sql} ORDER BY {order_by}", params
        ).fetchall()
        # Every read carries the forecast, so no screen has to recompute a
        # rate in the browser --- the defect class that made several screens
        # restate a flat 5% instead of the partner's real tier.
        return [with_commission_projection(dict(r)) for r in rows]
    finally:
        conn.close()


def get_leads_page(user: dict, filters: dict = None, sort: str = None,
                   direction: str = None, page: int = 1,
                   per_page: int = LEADS_PAGE_SIZE, search_columns=None):
    """Return one page of leads and the total number of matches.

    The list screen shows twenty-five rows and used to fetch every one of them
    to do it -- at ten thousand leads that is a four-second wait for the same
    twenty-five rows, and twelve seconds while the model is generating. The
    database does the filtering, ordering and slicing here instead.

    `total` counts the matches, not the page, because the screen has to say
    "1-25 z 400" and draw the right number of page buttons. It comes out of the
    same query as the rows, over the same conditions, so the two cannot drift.

    `search_columns` narrows where `q` looks (see _lead_list_conditions); the
    app leaves it None and searches every LEAD_SEARCH_COLUMNS column.
    """
    filters = _validate_lead_filters(filters)
    order_by = _parse_lead_sort(sort, direction)
    page = max(1, int(page or 1))
    per_page = max(1, min(int(per_page or LEADS_PAGE_SIZE), LEADS_MAX_PAGE_SIZE))
    offset = (page - 1) * per_page

    conn = get_conn()
    try:
        where_sql, params = _lead_list_conditions(user, filters, search_columns)
        rows = conn.execute(
            f"""SELECT l.*, u.name AS partner_name, u.company AS partner_company,
                       COALESCE(p.name_en, '') AS product_en,
                       COUNT(*) OVER () AS _total
                {_LEAD_LIST_FROM}{where_sql}
                ORDER BY {order_by} LIMIT ? OFFSET ?""",
            [*params, per_page, offset],
        ).fetchall()

        if rows:
            total = int(rows[0]["_total"])
        else:
            # An empty page carries no window-function total with it, and that
            # happens for a real reason: a producer on page 9 who narrows the
            # filter until only two leads match still needs to be told there
            # are two, or the screen offers no way back to them.
            total = int(conn.execute(
                f"SELECT COUNT(*) AS total {_LEAD_LIST_FROM}{where_sql}", params,
            ).fetchone()["total"] or 0)

        leads = []
        for row in rows:
            record = dict(row)
            record.pop("_total", None)
            leads.append(with_commission_projection(record))
        return leads, total
    finally:
        conn.close()


def get_lead(lead_id: str, user: dict):
    lead_id = _parse_lead_id(lead_id)
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT l.*, u.name AS partner_name, u.company AS partner_company,
                      COALESCE(p.name_en, '') AS product_en
               FROM leads l
               JOIN users u ON u.id = l.partner_id
               LEFT JOIN products p ON p.id = l.product_id
               WHERE l.id = ?""",
            (lead_id,)
        ).fetchone()

        if row is None:
            return None

        lead = dict(row)

        # Partners can only see their own
        if user["role"] == "partner" and lead["partner_id"] != user["id"]:
            return None

        return with_commission_projection(lead)
    finally:
        conn.close()


def find_client_duplicates(conn, nip, website):
    """Return the id and owner of existing leads whose stored client identity matches.

    Matches when the stored ``nip`` is non-empty and equal to ``nip``, or
    the stored ``website`` is non-empty and equal to ``website``. Callers
    must pass already-normalized values (see ``parse_nip``/``parse_website``)
    -- this function performs no normalization of its own. Ordered by
    ``created_at``, ``id`` so the first submitter is always first.

    A lead the producer REJECTED is not a claim on the client and takes no
    place in the queue: counting it would cut the next partner's share.

    The queue spans every partner, which row-level security (migration 0016)
    would hide from a partner, so this one lookup runs with the system
    context -- and returns only the lead id and its owner, never another
    partner's client data. Callers use the count and recognise their own row.
    """
    with system_context("duplicate-client queue lookup"):
        return conn.execute(
            """SELECT id, partner_id FROM leads
               WHERE ((nip != '' AND nip = ?) OR (website != '' AND website = ?))
                 AND COALESCE(registration_status, '') != 'rejected'
               ORDER BY created_at, id""",
            (nip, website),
        ).fetchall()


def _refuse_own_duplicate(duplicates, partner_id):
    """A partner cannot queue behind himself for his own client.

    Otherwise a second submission would take the next queue place and halve
    their own share. Named by id: it is their own lead.
    """
    if partner_id is None:
        return
    for row in duplicates:
        if row["partner_id"] == partner_id:
            raise ValueError(
                t("You have already submitted this client ({lead}). "
                  "Complete that submission instead of creating a new one.", lead=row['id'])
            )


_LEAD_PRECHECK_FIELDS = ("nip", "website")


def _validate_lead_precheck(data):
    values = parse_allowed_fields(data, _LEAD_PRECHECK_FIELDS, N_("Duplicate check data"))
    return {
        "nip": _normalize_optional_identity(values.get("nip"), parse_nip),
        "website": _normalize_optional_identity(values.get("website"), parse_website),
    }


def precheck_duplicate(user: dict, data: dict):
    """Report whether a client identity already exists and the queue
    position/share a new submission from ``user`` would take.

    Returns only the caller's own prospective position, share and the
    active policy word -- never any field identifying another submitter.
    Uses the same normalizers and duplicate lookup as
    ``create_lead`` so the preview and the real outcome always agree.
    """
    data = _validate_lead_precheck(data)
    conn = get_conn()
    try:
        duplicates = find_client_duplicates(conn, data["nip"], data["website"])
        own = next((row["id"] for row in duplicates if row["partner_id"] == user.get("id")), None)
        if own is not None:
            # His own earlier lead: say so, and do not quote a queue place he
            # could never take (create_lead refuses the submission).
            return {"duplicate": True, "own_lead_id": own, "queue_position": None,
                    "share": None, "policy": get_setting("lead_duplicate_policy") or "queue"}
        duplicate_policy = get_setting("lead_duplicate_policy") or "queue"
        if duplicate_policy == "allow":
            queue_position = 1
        else:
            queue_max = int(get_setting("lead_queue_max") or 5)
            queue_position = min(len(duplicates) + 1, queue_max)

        queue_shares = [
            float(part.strip())
            for part in (get_setting("lead_queue_shares") or "100").split(",")
        ]
        queue_index = min(queue_position, len(queue_shares)) - 1
        share = queue_shares[queue_index]

        return {
            "duplicate": bool(duplicates),
            "queue_position": queue_position,
            "share": share,
            "policy": duplicate_policy,
        }
    finally:
        conn.close()


def preview_lead_submission(user: dict, data: dict):
    """Validate a partner submission and return its current non-mutating preview."""
    _require_partner_submission(user, data)
    clean = _validate_lead_create(data)
    conn = get_conn()
    try:
        resolved, preview = _resolve_product_and_queue(conn, clean, partner_id=user["id"])
        return {"data": resolved, "preview": preview}
    finally:
        conn.close()


def submit_lead(user: dict, data: dict):
    """Canonical authenticated lead submission used by HTTP and AI."""
    _require_partner_submission(user, data)
    return create_lead(user, data)


# ─── SHARED CREATION PRIMITIVE ──────────────────
#
# One insert path, two entry points. A partner's own submission and a
# producer's assignment differ in exactly two facts -- who owns the row, and
# whether it is already in the sale -- and in nothing else: the same catalogue
# rules, the same duplicate policy, the same queue arithmetic, the same custom
# fields. Both run inside the CALLER's BEGIN IMMEDIATE, so the schema the
# answers were checked against, the partner who was active, the product that
# was offered, the ID and the queue position are all read under the same lock
# that writes the row. A wrapper that created a lead and then accepted it in a
# second commit would leave a pending lead behind whenever the second one
# failed, which is the state this primitive exists to make impossible.


def _require_current_lead_schema(conn, submitted_version):
    """Refuse a form filled in before the producer edited it.

    Read on the caller's locked connection rather than through a second one:
    the version that admits the answers must be the version that is still
    current at insert time.
    """
    if submitted_version is None:
        return
    if str(submitted_version) != lead_fields.schema_version(conn=conn):
        raise LeadFieldsChanged(
            t("The vendor changed the submission form. Refresh and check your answers "
              "before sending.")
        )


def _record_lead_event(conn, lead_id, actor_id, event_type, payload):
    """Append to a lead's immutable history from inside the caller's
    transaction. The same table LeadWorkflowContext.record_event writes, so a
    creation and every later change read back as one history."""
    conn.execute(
        """INSERT INTO lead_workflow_events
                  (lead_id, actor_id, event_type, payload_json, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (lead_id, actor_id, event_type,
         json.dumps(payload, ensure_ascii=False), datetime.utcnow().isoformat()),
    )


def _insert_lead_in_transaction(conn, data, owner_id, prepared_field_values,
                                *, status, registration_status):
    """Write one lead row and its answers. Returns (id, resolved data)."""
    now = datetime.utcnow().isoformat()
    new_id = next_lead_id(conn)

    # Catalogue selection and queue facts are resolved by the same helper as
    # the confirmation preview. Internal seed/test callers may omit
    # product_id; authenticated submissions cannot.
    # Only the partner's own submission is refused as his duplicate; a
    # producer assigning a deal to him is a deliberate decision (lead.assign).
    data, submission_preview = _resolve_product_and_queue(
        conn, data, partner_id=owner_id if registration_status == "pending" else None
    )

    conn.execute(
        """INSERT INTO leads
           (id, client_name, client_email, client_phone, company, value,
            notes, sector, nip, website, product, product_id, quantity, queue_position,
            status, registration_status, partner_id, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            new_id,
            data["client_name"],
            data["client_email"],
            data["client_phone"],
            data["company"],
            data["value"],
            data["notes"],
            data["sector"],
            data["nip"],
            data["website"],
            data["product"],
            data["product_id"],
            data["quantity"],
            submission_preview["queue_position"],
            status,
            registration_status,
            owner_id,
            now, now,
        )
    )
    lead_fields.save_values_in_transaction(conn, new_id, prepared_field_values)
    return new_id, data


def _run_after_commit(lead_id, effects):
    """Run side effects of an already committed write, each on its own.

    A failure is logged by type only (the text may carry customer data) and
    never propagates: the write it reports on is already durable.
    """
    for effect in effects:
        try:
            effect()
        except Exception as exc:
            logger.warning(
                "Lead effect failed after commit: lead=%s type=%s",
                lead_id, type(exc).__name__,
            )


def create_lead(user: dict, data: dict):
    """Create a new lead for the authenticated partner."""
    data = _validate_lead_create(data)
    value = data["value"]

    submitted_values = data.pop("lead_field_values", None)
    submitted_version = data.pop("lead_fields_version", None)

    conn = get_conn()
    try:
        conn.begin()
        conn.execute("SELECT pg_advisory_xact_lock(734102620)")
        try:
            # Validated before the row is written and under the same lock, so
            # a bad answer cannot leave a half-filled submission behind and a
            # required field added meanwhile cannot be silently skipped.
            _require_current_lead_schema(conn, submitted_version)
            prepared_field_values = lead_fields.validate_values(
                submitted_values, fields=lead_fields.list_fields(conn=conn)
            )
            new_id, data = _insert_lead_in_transaction(
                conn, data, owner_id=user["id"],
                prepared_field_values=prepared_field_values,
                # No sales stage yet: a freshly submitted deal is waiting for
                # the producer's decision, not sitting anywhere in the sale.
                status=None, registration_status="pending",
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        # Return the newly created lead
        row = conn.execute(
            """SELECT l.*, u.name AS partner_name, u.company AS partner_company,
                      COALESCE(p.name_en, '') AS product_en
               FROM leads l
               JOIN users u ON u.id = l.partner_id
               LEFT JOIN products p ON p.id = l.product_id
               WHERE l.id = ?""",
            (new_id,)
        ).fetchone()
        result = dict(row)
        client_name = result["client_name"]

        # Past this line the lead is saved. Each effect runs on its own, so a
        # failing notification or mailer can neither report the saved lead as
        # an error nor skip the audit entry and the effects after it.
        _run_after_commit(new_id, (
            lambda: audit.log(user, "lead.create", "lead", new_id,
                              f"{client_name} / {result['company']} / {value:,.0f} PLN"),
            # The owning partner's in-app receipt for the submission.
            lambda: notif.notify_partner_lead_submitted(user["id"], new_id, client_name),
            lambda: notif.notify_all_admins(
                "info",
                t("New lead: {client}", client=notif.quoted(client_name)),
                t("Partner: {partner} · Value: {amount} PLN",
                  partner=result['partner_name'], amount=f"{value:,.0f}"),
                "leads.html",
                title_key="ntf_new_lead_title",
                body_key="ntf_new_lead_body",
                params={"client": client_name,
                        "partner": result["partner_name"],
                        "value": f"{value:,.0f}"},
            ),
            lambda: mailer.send_lead_created(
                user.get("email", ""), user.get("name", ""), new_id, client_name
            ),
        ))

        blocks.emit("lead.created", new_id, user["id"])

        return result
    finally:
        conn.close()


# ─── PRODUCER ASSIGNMENT ───────────────


def _assignment_payload_hash(owner_id, schema_revision, values):
    """Identity of one assignment attempt. Stable key order: JSON object
    ordering is not an operation identity."""
    payload = json.dumps(["lead.assign", owner_id, schema_revision, values],
                         sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _authorize_assignment_actor(conn, user):
    """The producer, re-read live inside the transaction -- a session whose
    account was deactivated or demoted since login does not assign."""
    if not can_assign_lead(user):
        raise PermissionError(t("Only the vendor assigns leads."))
    actor = conn.execute("SELECT id, role, active FROM users WHERE id=?",
                         (user.get("id"),)).fetchone()
    if actor is None or not actor["active"] or actor["role"] != user.get("role"):
        raise PermissionError(t("Access denied."))
    if not can_assign_lead(dict(actor)):
        raise PermissionError(t("Only the vendor assigns leads."))


def _assignment_owner(conn, partner_id):
    """The chosen owner, required to be an active partner at write time."""
    owner = conn.execute(
        "SELECT id, name, email, role, active FROM users WHERE id=?", (partner_id,)
    ).fetchone()
    if owner is None or not owner["active"] or owner["role"] != "partner":
        raise ValueError(t("Choose an active partner."))
    return owner


def create_assigned_lead(user: dict, data: dict):
    """
    The producer creates a lead FOR a partner and accepts it in one action.

    Actor and owner are deliberately separate: the row belongs to the chosen
    partner (their commission, their board, their editing rights), while the
    history and the audit trail record the producer who actually did it. This
    is new work only -- it never moves an existing lead to another partner.
    """
    values = parse_allowed_fields(data, _LEAD_ASSIGN_FIELDS, N_("Assignment data"))
    partner_id = parse_integer_id(values.pop("partner_id", None), N_("Partner"))
    schema_revision = parse_required_string(
        values.pop("schema_revision", None), "schema_revision", max_length=64
    )
    if not parse_boolean_flag(values.pop("confirm_accept", None), "confirm_accept"):
        raise ValueError(
            t("Confirm creating the lead and accepting it into the first stage.")
        )
    operation_id = parse_required_string(
        values.pop("operation_id", None), "operation_id", max_length=128
    )
    if not operation_id.isascii():
        raise ValueError(t("Field 'operation_id' may contain ASCII characters only."))

    # The producer fills in the partner's own form, so it is held to the same
    # standard: the identity fields a partner cannot skip, they cannot either.
    for field, label in _LEAD_IDENTITY_REQUIRED_FIELDS:
        if not values.get(field):
            raise ValueError(t("Field '{field}' is required.", field=t(label)))

    payload_hash = _assignment_payload_hash(partner_id, schema_revision, values)
    clean = _validate_lead_create(values)
    submitted_values = clean.pop("lead_field_values", None)
    clean.pop("lead_fields_version", None)

    conn = get_conn()
    try:
        conn.begin()
        conn.execute("SELECT pg_advisory_xact_lock(734102620)")
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                     (f"operation:{user['id']}:{operation_id}",))
        try:
            _authorize_assignment_actor(conn, user)

            # Replay before anything is written: an identical retry of a
            # request whose response never arrived returns the lead it already
            # created, and the same key carrying different data is refused
            # rather than creating a second one.
            previous = conn.execute(
                """SELECT lead_id, payload_hash, result_json
                     FROM lead_workflow_operations
                    WHERE actor_id=? AND operation_id=?""",
                (user["id"], operation_id),
            ).fetchone()
            if previous is not None:
                if previous["payload_hash"] != payload_hash:
                    raise OperationConflictError()
                conn.rollback()
                return json.loads(previous["result_json"])

            owner = _assignment_owner(conn, partner_id)
            # Accepting is what puts the deal into the sale, so the entry
            # point is read live here exactly as apply_registration_action
            # reads it -- and under this transaction's lock.
            stage = conn.execute(
                """SELECT * FROM lead_stages
                    WHERE is_terminal = 0 AND active = 1
                    ORDER BY sort_order ASC, id ASC LIMIT 1"""
            ).fetchone()
            if stage is None:
                raise LeadWorkflowConflict(
                    t("There is no active sales stage. Set up the pipeline before assigning"
                      " leads."),
                    "no_pipeline_stage",
                )

            _require_current_lead_schema(conn, schema_revision)
            prepared_field_values = lead_fields.validate_values(
                submitted_values, fields=lead_fields.list_fields(conn=conn)
            )
            new_id, resolved = _insert_lead_in_transaction(
                conn, clean, owner_id=owner["id"],
                prepared_field_values=prepared_field_values,
                status=stage["key"], registration_status="accepted",
            )
            _record_lead_event(conn, new_id, user["id"], "lead.assign", {
                "owner_id": owner["id"],
                "stage": stage["key"],
                "confirmed_accept": True,
            })
            _record_lead_event(conn, new_id, user["id"], "registration.accept", {
                "from": None, "to": "accepted", "assigned": True,
            })
            result = _read_lead_for_result(conn, new_id)
            conn.execute(
                """INSERT INTO lead_workflow_operations
                          (actor_id, operation_id, payload_hash, lead_id,
                           result_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (user["id"], operation_id, payload_hash, new_id,
                 json.dumps(result, ensure_ascii=False),
                 datetime.utcnow().isoformat()),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        # Past this line the write is durable; nothing below can unsay it.
        owner_id, owner_name, owner_email = owner["id"], owner["name"], owner["email"]
        client_name = result["client_name"]
        for effect in (
            lambda: notif.create_notification(
                owner_id, "success", t("New lead from the vendor: {client}", client=notif.quoted(client_name)),
                t("The vendor assigned you a lead and accepted it for sale."),
                "leads.html",
                title_key="ntf_lead_assigned_title",
                body_key="ntf_lead_assigned_body",
                params={"client": client_name}),
            lambda: audit.log(
                user, "lead.assign", "lead", new_id,
                f"{client_name} / {resolved['company']} → {owner_name}"),
            lambda: mailer.send_lead_created(
                owner_email or "", owner_name or "", new_id, client_name),
        ):
            try:
                effect()
            except Exception as exc:
                logger.warning(
                    "Lead assignment effect failed after commit: lead=%s type=%s",
                    new_id, type(exc).__name__,
                )
        blocks.emit("lead.assign", new_id, user["id"])
        return result
    finally:
        conn.close()


# ─── AFFILIATE ORDERS ───────────────


def _first_stage_of_kind(conn, kind):
    return conn.execute(
        """SELECT key FROM lead_stages WHERE kind = ? AND active = 1
            ORDER BY sort_order ASC, id ASC LIMIT 1""",
        (kind,),
    ).fetchone()


def _affiliate_draft(partner_id, value):
    """The lead row an affiliate order is priced as: the partner's own rates,
    first in the queue (the shop sold it once), no per-deal correction."""
    return {"partner_id": partner_id, "value": float(value), "queue_position": 1,
            "commission_override_pct": None}


def affiliate_commission_forecast(partner_id, value):
    """What an order of `value` would earn `partner_id` once approved."""
    return _projected_commission(_affiliate_draft(partner_id, value))["amount"]


def create_affiliate_lead(conn, *, partner_id, order_id, value, actor_id):
    """Record an approved shop order as a won lead of its partner.

    Runs inside the caller's transaction. The commission comes from the same
    rules as any won lead and is stamped whatever lead_auto_commission says:
    the approval is the producer's decision about this very amount. The win is
    confirmed by the approval too, so the order reaches payouts without a
    second click. The shop knows no NIP, website or product, and the buyer's
    name and e-mail are never stored, so the lead carries the order id instead.

    Returns (lead id, commission).
    """
    stage = _first_stage_of_kind(conn, "win")
    if stage is None:
        raise LeadWorkflowConflict(
            t("There is no active won stage. Add one before approving shop orders."),
            "no_win_stage",
        )
    draft = _affiliate_draft(partner_id, value)
    commission = _projected_commission(draft)["amount"]
    lead_id = next_lead_id(conn)
    now = datetime.utcnow().isoformat()
    conn.execute(
        """INSERT INTO leads
                  (id, client_name, client_email, company, value, status,
                   registration_status, partner_id, commission, won_at,
                   settlement_status, win_confirmed_at, win_confirmed_by,
                   source, external_order_id, created_at, updated_at)
           VALUES (?, ?, '', '', ?, ?, 'accepted', ?, ?, ?, 'unpaid', ?, ?,
                   'affiliate', ?, ?, ?)""",
        (lead_id, order_id, draft["value"], stage["key"], partner_id, commission,
         now, now, actor_id, order_id, now, now),
    )
    if actor_id is not None:
        _record_lead_event(conn, lead_id, actor_id, "affiliate.approve", {
            "order_id": order_id, "stage": stage["key"], "commission": commission,
        })
    return lead_id, commission


def reverse_affiliate_lead(conn, lead_id, actor_id):
    """Take a refunded affiliate order out of the win, inside the caller's
    transaction: the lead moves to the first lost stage and its unpaid
    commission is cleared, as reopening an unpaid win does. A commission
    already paid is never touched here."""
    row = conn.execute(
        "SELECT * FROM leads WHERE id = ? AND source = 'affiliate' FOR UPDATE", (lead_id,)
    ).fetchone()
    if row is None:
        raise LeadNotFoundError(t("Lead not found."))
    if row["commission_paid_at"] or row["settlement_status"] == "paid":
        raise LeadWorkflowConflict(
            t("The commission has already been paid, so the refund is recorded for "
              "information only."),
            "settlement_paid",
        )
    stage = _first_stage_of_kind(conn, "lose")
    if stage is None:
        raise LeadWorkflowConflict(
            t("There is no active lost stage. Add one before reversing a refunded order."),
            "no_lose_stage",
        )
    conn.execute(
        """UPDATE leads
              SET status = ?, won_at = NULL, commission = NULL, settlement_status = NULL,
                  win_confirmed_at = NULL, win_confirmed_by = NULL,
                  revision = revision + 1, updated_at = ?
            WHERE id = ?""",
        (stage["key"], datetime.utcnow().isoformat(), lead_id),
    )
    _record_lead_event(conn, lead_id, actor_id, "affiliate.reverse", {
        "from": row["status"], "to": stage["key"], "prior_commission": row["commission"],
    })
    return row["partner_id"], row["commission"]


def _commission_components(row):
    """Return the tier / recurring-bonus / cash-bonus split behind a lead's
    commission, from the one source of rules.

    The producer's lead detail has always had three lines for this, reading
    `commission_breakdown` / `tier_commission` / `recurring_bonus` /
    `cash_bonus` --- fields no backend code ever populated, so the whole
    amount always rendered as "stawka poziomu" with the other two rows
    sitting at zero. They are real now.
    """
    summary = partner_bonuses.get_partner_summary(row["partner_id"])
    base_rate = float((summary or {}).get("base_rate") or 0)
    bonus_percent = sum(
        float(b["effective_value"]) for b in (summary or {}).get("bonus_percent", [])
    )
    return {
        "base_rate": base_rate,
        "bonus_percent": round(bonus_percent, 4),
        # Cash bonuses are paid alongside the deal, never folded into its
        # percentage --- the rule api/partner_bonuses.py already enforces.
        "cash": float((summary or {}).get("total_cash") or 0),
        "tier_name": (summary or {}).get("tier_name") or t("no tier"),
        # The producer's own English wording of that same tier,
        # read through Ui.producerLabel wherever this flows to a screen.
        # Empty (never the fallback text above) when untranslated or when
        # the partner has no tier -- get_partner_summary's LEFT JOIN already
        # returns None for both, and this is data, not the copy fallback
        # "no tier" is.
        "tier_name_en": (summary or {}).get("tier_name_en") or "",
        "total_percent": float((summary or {}).get("total_percent") or 0),
    }


def _projected_commission(row):
    """Return the commission this lead WOULD earn, from the single source of
    rules, with no reference to what stage it is on.

    The commission is visible at every stage but not payable until the win,
    so a partner working a deal can see what it is worth. It is recomputed on
    every read and stored only when the deal is actually won (see
    _stamp_commission).

    The arithmetic is backend-owned:
        value x tier total_percent% x queue_share%
    Cash bonuses never enter it.
    """
    components = _commission_components(row)
    tier_name = components["tier_name"]
    tier_name_en = components["tier_name_en"]

    # A producer-agreed rate for this one deal replaces the partner's tier
    # rate wholesale --- it is not added to it, and it is not scaled by
    # the bonus percentages either, because "8% on this lead" means 8%.
    override_pct = _override_pct(row)
    total_percent = (
        override_pct if override_pct is not None else components["total_percent"]
    )

    queue_shares = [
        float(value.strip())
        for value in (get_setting("lead_queue_shares") or "100").split(",")
    ]
    queue_position = max(int(row["queue_position"] or 1), 1)
    queue_index = min(queue_position, len(queue_shares)) - 1
    queue_share = queue_shares[queue_index]

    scale = (row["value"] or 0) * (queue_share / 100)
    amount = round(scale * (total_percent / 100), 2)
    if override_pct is None:
        tier_amount = round(scale * (components["base_rate"] / 100), 2)
        bonus_amount = round(scale * (components["bonus_percent"] / 100), 2)
    else:
        # Under a correction the whole figure IS the agreed rate; splitting it
        # across tier and bonus lines would invent a breakdown nobody agreed.
        tier_amount = amount
        bonus_amount = 0.0

    return {
        "amount": amount,
        "tier_name": tier_name,
        "tier_name_en": tier_name_en,
        "total_percent": total_percent,
        "queue_share": queue_share,
        "override_pct": override_pct,
        "breakdown": {
            "tier": tier_amount,
            "recurring": bonus_amount,
            "cash": components["cash"],
        },
        "context": (
            t("tier: {tier}; rate: {rate}%", tier=tier_name, rate=f"{total_percent:g}")
            + (t(" (corrected)") if override_pct is not None else "")
            + t("; queue share: {share}%", share=f"{queue_share:g}")
        ),
        # The same sentence as a key the audit screen composes in the reader's
        # language. The literal above stays: it is what older rows carry.
        "context_key": (
            "audit_detail_commission_context_override" if override_pct is not None
            else "audit_detail_commission_context"
        ),
        "context_params": {
            "tier": tier_name,
            "rate": f"{total_percent:g}",
            "share": f"{queue_share:g}",
        },
    }


def _override_pct(row):
    """Read the per-deal rate correction from a row or dict, tolerating a
    row shape that predates the column."""
    try:
        return row["commission_override_pct"]
    except (KeyError, IndexError):
        return None


def with_commission_projection(lead):
    """Attach the projected commission to a lead dict for every read path.

    `commission` stays what it always was: the amount actually recorded
    against the deal, NULL until it is won or a producer overrides it.
    `commission_projected` is the forecast, present at every stage.
    Keeping them as two fields is what lets a screen show "przewidywana"
    versus "naliczona" without guessing.
    """
    projection = _projected_commission(lead)
    lead["commission_projected"] = projection["amount"]
    lead["commission_rate"] = projection["total_percent"]
    lead["tier_name"] = projection["tier_name"]
    lead["tier_name_en"] = projection["tier_name_en"]
    lead["queue_share"] = projection["queue_share"]
    lead["commission_breakdown"] = projection["breakdown"]
    lead["tier_commission"] = projection["breakdown"]["tier"]
    lead["recurring_bonus"] = projection["breakdown"]["recurring"]
    lead["cash_bonus"] = projection["breakdown"]["cash"]
    return lead


def _commission_projection(row, data, target_stage):
    """Return the commission effect of an update, shared by preview and write.

    "Calculated" now means "this update wins the deal, so the forecast
    becomes the recorded amount" --- not "this update hit the trigger
    stage". An explicit commission in the payload still wins over the
    formula, at any stage, which is what makes a correction possible before
    the sale closes.
    """
    projection = {
        "commission": row["commission"],
        "calculated": False,
        "tier_name": None,
        "tier_name_en": None,
        "total_percent": None,
        "queue_share": None,
        "context": None,
    }
    is_win = bool(target_stage and target_stage["kind"] == "win")

    if "commission" in data and data["commission"] is not None:
        projection["commission"] = data["commission"]
        projection["context"] = f"prowizja jawna: {data['commission']:g} PLN"
        return projection

    # A rate correction recomputes the money from the corrected rate, so the
    # producer states a percentage and the backend still owns the arithmetic
    # --- the rule that stopped screens restating a flat rate of their own.
    if "commission_override_pct" in data:
        corrected = dict(row)
        corrected["commission_override_pct"] = data["commission_override_pct"]
        forecast = _projected_commission(corrected)
        projection["context"] = forecast["context"]
        projection["total_percent"] = forecast["total_percent"]
        # Only a deal already carrying a recorded amount has one to restate;
        # an open deal just gets a new forecast.
        if row["commission"] is not None:
            projection["commission"] = forecast["amount"]
            projection["calculated"] = True
        return projection

    # Stamp the forecast onto the deal the moment it is won, and never
    # re-stamp: a commission already recorded is a decision, not a cache.
    if not is_win or row["commission"] is not None:
        return projection
    if get_setting("lead_auto_commission") != "1":
        return projection

    forecast = _projected_commission(row)
    projection.update({
        "commission": forecast["amount"],
        "calculated": True,
        "tier_name": forecast["tier_name"],
        "tier_name_en": forecast["tier_name_en"],
        "total_percent": forecast["total_percent"],
        "queue_share": forecast["queue_share"],
        "context": forecast["context"],
    })
    return projection


def _terminal_exit_attempted(row, data):
    """True when `data` would move a currently-terminal lead's pipeline
    position -- its sales stage directly, or the registration decision that
    put it on the pipeline at all (rejecting an already-accepted, already-
    closed deal un-terminals it via `status = NULL` exactly as surely as a
    direct stage change would).

    Reopening a closed lead is a dedicated, audited action reserved for a
    later plan -- until it exists, this is what every caller of
    `update_lead` shares, so no adapter (admin PATCH, AI-confirmed action,
    bulk decision, a future direct caller) can quietly bypass it by using a
    different route to the same write. A commission-only correction on an
    already-closed deal is untouched: it never sets `status` or moves
    `registration_status` off `accepted`.

    Re-affirming the SAME terminal stage (`status` sent back as the value it
    already is) is not an exit and is left alone -- this is the same
    true-no-op shape `move_partner_lead` already treats specially, and a
    retried "won" write that lands twice must stay free to not restate
    `won_at`/commission rather than being punished for its own idempotency.
    """
    current_stage = lead_stages.get_by_key(row["status"]) if row["status"] else None
    if not current_stage or not current_stage["is_terminal"]:
        return False
    if "status" in data and data["status"] != row["status"]:
        return True
    if "registration_status" in data and data["registration_status"] != "accepted":
        return True
    return False


def _guard_commission_correction(row, data, user):
    """
    Refuse a commission write the lead's settlement no longer allows.

    A paid commission is payout evidence: history and per-year totals are
    read from leads.commission, so it must not change after the money moved.
    Once the producer confirmed the win the amount is owed, and changing
    what is owed is the account owner's decision, like paying it.
    """
    if "commission" not in data and "commission_override_pct" not in data:
        return
    if row["commission_paid_at"] or row["settlement_status"] == "paid":
        raise LeadWorkflowConflict(
            t("The commission has already been paid out. Reverse the payout first."),
            "settlement_paid",
        )
    if row["win_confirmed_at"] and not can_pay_commission(user):
        raise PermissionError(
            t("Only an administrator corrects a confirmed commission.")
        )


def preview_lead_update(lead_id: str, user: dict, data: dict):
    """Validate an admin update and expose its current effect without writing."""
    lead_id = _parse_lead_id(lead_id)
    clean = _validate_lead_update(data)
    if not is_staff(user):
        raise PermissionError(t("Only an administrator can edit the lead."))

    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if row is None:
            raise ValueError(t("The lead does not exist."))
        if _terminal_exit_attempted(row, clean):
            raise LeadWorkflowConflict(
                t("The lead is read-only. The vendor can reopen it."),
                "lead_read_only",
            )
        _guard_commission_correction(row, clean, user)
        target_stage = lead_stages.get_by_key(clean["status"]) if "status" in clean else None
        projection = _commission_projection(row, clean, target_stage)
        current_stage = lead_stages.get_by_key(row["status"])
        return {
            "data": clean,
            "snapshot": {
                "updated_at": row["updated_at"],
                "status": row["status"],
                "commission": row["commission"],
            },
            "preview": {
                "lead_id": lead_id,
                "client_name": row["client_name"],
                "old_status": row["status"],
                "old_status_name": (current_stage or {}).get("name") or row["status"],
                "new_status": clean.get("status", row["status"]),
                "new_status_name": (target_stage or {}).get("name") or clean.get("status", row["status"]),
                "new_status_kind": (target_stage or {}).get("kind"),
                "old_registration_status": row["registration_status"],
                "new_registration_status": clean.get(
                    "registration_status", row["registration_status"]
                ),
                "is_win_transition": bool(
                    target_stage and target_stage["kind"] == "win"
                ),
                "commission": projection["commission"],
                "commission_calculated": projection["calculated"],
                "tier_name": projection["tier_name"],
                "tier_name_en": projection["tier_name_en"],
                "total_percent": projection["total_percent"],
                "queue_share": projection["queue_share"],
            },
        }
    finally:
        conn.close()


def _authorize_lead_editor(conn, user):
    """The editing session's account, re-read under the lead's lock: a
    session whose account was deactivated or demoted since sign-in does not
    edit the lead."""
    actor = conn.execute("SELECT id, role, active FROM users WHERE id=?",
                         (user.get("id"),)).fetchone()
    if actor is None or not actor["active"] or actor["role"] != user.get("role"):
        raise PermissionError(t("Access denied."))
    if not is_staff(dict(actor)):
        raise PermissionError(t("Only an administrator can edit the lead."))


def update_lead(lead_id: str, user: dict, data: dict,
                expected_revision=None, operation_id=None):
    """
    Update status and/or commission. Only admin can change status/commission.

    `expected_revision` and `operation_id` are mandatory for HTTP callers and
    enforced in handle_update, not here: trusted internal callers (the demo
    seed, confirmed AI actions) read the current version under the core's own
    lock instead of carrying one across a request boundary.
    """
    lead_id = _parse_lead_id(lead_id)
    data = _validate_lead_update(data)
    # The identity of this request for the replay table: a retry of the same
    # change returns its saved result, and the same operation id carrying a
    # different change is refused instead of reaching the unique index.
    payload_hash = _workflow_payload_hash(
        lead_id, "lead.update",
        json.dumps(data, sort_keys=True, ensure_ascii=False), expected_revision,
    )
    conn = get_conn()
    try:
        # One transaction, owned here and opened by the shared workflow core
        # (api/db.py): the state change and its public decision message live
        # or die together. The version travels in from the caller, so two
        # producers editing the same lead cannot silently overwrite each
        # other -- the second one is told to look again.
        with lead_workflow_transaction(
            conn, lead_id, actor_id=user["id"],
            expected_revision=expected_revision, operation_id=operation_id,
            payload_hash=payload_hash,
            authorize=lambda locked: _authorize_lead_editor(conn, user),
        ) as ctx:
            if ctx.replayed:
                return ctx.result
            row = ctx.row

            if not is_staff(user):
                raise PermissionError(t("Only an administrator can edit the lead."))

            # This IS the shared domain core ARCHITECTURE.md means by
            # "no adapter bypasses the guard": the
            # HTTP admin PATCH and every AI-confirmed `update_lead_status`
            # action both write through this one function, so blocking here
            # closes both routes at once, not just the one the UI happens
            # to show a popup for.
            if _terminal_exit_attempted(row, data):
                raise LeadWorkflowConflict(
                    t("The lead is read-only. The vendor can reopen it."),
                    "lead_read_only",
                )
            _guard_commission_correction(row, data, user)

            updates = []
            params  = []
            commission_context = None

            # The stage a status update targets, looked up once and reused by the
            # commission, notification, audit and email logic below -- they key
            # off this stage's kind, never off a literal status word, so a
            # renamed or reordered pipeline never silently changes behaviour.
            target_stage = lead_stages.get_by_key(data["status"]) if "status" in data else None
            is_win_transition = bool(target_stage and target_stage["kind"] == "win")
            is_lose_transition = bool(target_stage and target_stage["kind"] == "lose")

            # The registration axis: the producer's decision about the deal,
            # independent of how far the sale has got.
            new_registration = data.get("registration_status")
            current_registration = row["registration_status"]

            commission_projection = _commission_projection(row, data, target_stage)

            if "registration_status" in data:
                updates.append("registration_status = ?")
                params.append(new_registration)
                if new_registration == "accepted" and not row["status"]:
                    # Accepting a deal is what puts it into the sales pipeline.
                    # It starts at the first producer-defined stage; before this
                    # moment leads.status is NULL, because an unapproved deal is
                    # not anywhere in the sale.
                    first_stage = lead_stages.get_first_pipeline_stage()
                    if first_stage:
                        updates.append("status = ?")
                        params.append(first_stage["key"])
                elif new_registration in ("rejected", "pending", "needs_info"):
                    # Moving back off "accepted" takes the deal out of the
                    # pipeline again rather than leaving it parked on a stage
                    # that would keep counting into pipeline value.
                    updates.append("status = NULL")

            if "status" in data:
                # A pipeline stage is only meaningful for an approved deal.
                effective_registration = new_registration or current_registration
                if effective_registration != "accepted":
                    raise ValueError(
                        t("The lead must be accepted before it can move to a sales stage.")
                    )
                updates.append("status = ?")
                params.append(data["status"])

                # Stamp when the sale closed, so the payout view can group by it
                # (G3). Set once: re-entering the win stage does not restate the
                # date a deal was won.
                if is_win_transition and row["won_at"] is None:
                    updates.append("won_at = ?")
                    params.append(datetime.utcnow().isoformat())
                    # A fresh win cycle's own settlement provenance: a commission this app just calculated cannot
                    # yet have been paid, so it starts 'unpaid' -- an
                    # explicit, bounded application assumption, never a
                    # payout claim. Only reopen_lead ever clears it back to
                    # NULL, and only review_reopen_eligibility ever moves it
                    # to 'unknown'/'paid'; this is the one place a fresh
                    # cycle begins.
                    updates.append("settlement_status = ?")
                    params.append(_initial_win_settlement(row))

                # Winning the deal turns the forecast into the recorded amount.
                if commission_projection["calculated"]:
                    updates.append("commission = ?")
                    params.append(commission_projection["commission"])
                    commission_context = commission_projection["context"]

            if "commission_override_pct" in data:
                updates.append("commission_override_pct = ?")
                params.append(data["commission_override_pct"])
                commission_context = commission_projection["context"]
                if commission_projection["calculated"]:
                    updates.append("commission = ?")
                    params.append(commission_projection["commission"])

            if "commission" in data and data["commission"] is not None:
                updates.append("commission = ?")
                params.append(data["commission"])
                commission_context = commission_projection["context"]

            if not updates:
                raise ValueError(t("No data to update."))

            updates.append("updated_at = ?")
            params.append(datetime.utcnow().isoformat())
            params.append(lead_id)

            conn.execute(
                f"UPDATE leads SET {', '.join(updates)} WHERE id = ?",
                params
            )
            comment_id = None
            if "registration_message" in data:
                comment_id = _add_public_comment(
                    conn, lead_id, user["id"], data["registration_message"]
                )
            ctx.touch()
            # The change joins the lead's history (and reaches blocks after
            # commit) like every other workflow write. Field names and states
            # only: no typed text.
            written = conn.execute(
                "SELECT status, registration_status, commission FROM leads WHERE id = ?",
                (lead_id,),
            ).fetchone()
            ctx.record_event("lead.update", {
                "fields": sorted(data),
                "from": row["status"], "to": written["status"],
                "registration_from": current_registration,
                "registration_to": written["registration_status"],
                "commission_changed": written["commission"] != row["commission"],
                "comment_id": comment_id,
            })
            ctx.result_builder = lambda: _read_lead_for_result(conn, lead_id)

            partner_id = row["partner_id"]
            client_name = row["client_name"]
            registration_changed = (
                "registration_status" in data and new_registration != current_registration
            )

            # Everything below runs after the commit, each effect on its own:
            # a notification, audit or mail failure can never turn a saved
            # change into an error response or skip the effects after it.
            # The audit entry goes first.
            def record_audit():
                result = ctx.result
                # Audit log -- the action key is derived from the value or stage key
                # itself (not a hardcoded English word), so a renamed pipeline still
                # produces a sensible, distinct entry with no code change.
                if registration_changed:
                    detail = f"{client_name} → {t(REGISTRATION_LABELS.get(new_registration, new_registration))}"
                    # The decision travels as its KEY, not as the Polish word it had
                    # when the entry was written; the audit screen resolves it.
                    audit.log(user, f"lead.{new_registration}", "lead", lead_id, detail,
                              detail_key="audit_detail_registration",
                              detail_params={"client": client_name,
                                             "status_key": f"reg_status_{new_registration}"})
                elif "status" in data:
                    detail = f"{client_name} → {data['status']}"
                    if commission_context:
                        detail += f" | {commission_context}"
                    action_key = (
                        f"lead.{data['status']}"
                        if (is_win_transition or is_lose_transition)
                        else "lead.update"
                    )
                    # The stage name is the producer's own word and stays as written.
                    # Only the commission clause after it is a sentence.
                    if commission_context:
                        audit.log(
                            user, action_key, "lead", lead_id, detail,
                            detail_key=commission_projection.get("context_key"),
                            detail_params={
                                "client": client_name,
                                "stage": data["status"],
                                **(commission_projection.get("context_params") or {}),
                            },
                        )
                    else:
                        audit.log(user, action_key, "lead", lead_id, detail)
                elif "commission_override_pct" in data:
                    previous = (
                        f"{row['commission_override_pct']:g}%"
                        if row["commission_override_pct"] is not None
                        else t("tier rate")
                    )
                    requested = data["commission_override_pct"]
                    now_value = t("tier rate") if requested is None else f"{requested:g}%"
                    audit.log(
                        user, "lead.commission_rate", "lead", lead_id,
                        t("{client} rate correction: {before} → {after} | {amount} PLN",
                          client=client_name, before=previous, after=now_value,
                          amount=f"{result['commission_projected']:g}"),
                    )
                elif "commission" in data:
                    # The previous amount is recorded alongside the new one: a
                    # correction the log cannot state the "before" of is not much of
                    # an audit trail.
                    previous = (
                        f"{row['commission']:g} PLN" if row["commission"] is not None
                        else t("none")
                    )
                    audit.log(
                        user, "lead.commission", "lead", lead_id,
                        t("{client} commission correction: {before} → {after} PLN",
                          client=client_name, before=previous,
                          after=f"{data.get('commission'):g}"),
                    )

            def notify():
                result = ctx.result
                if registration_changed and new_registration == "accepted":
                    notif.create_notification(
                        partner_id, "success",
                        t("Lead accepted: {client}", client=notif.quoted(client_name)),
                        t("Expected commission: {amount} PLN",
                          amount=f"{result['commission_projected']:,.0f}"),
                        "leads.html",
                        title_key="ntf_lead_accepted_title",
                        body_key="ntf_lead_accepted_body",
                        params={"client": client_name,
                                "amount": f"{result['commission_projected']:,.0f}"},
                    )
                elif registration_changed and new_registration == "rejected":
                    notif.create_notification(
                        partner_id, "error",
                        t("Lead rejected: {client}", client=notif.quoted(client_name)),
                        t("Contact the administrator for more information."),
                        "leads.html",
                        title_key="ntf_lead_rejected_title",
                        body_key="ntf_lead_rejected_body",
                        params={"client": client_name},
                    )
                elif registration_changed and new_registration == "needs_info":
                    # A value on the registration axis, not a stage key, so a
                    # producer deleting or reordering stages cannot stop their
                    # partners being told to supply information.
                    notif.create_notification(
                        partner_id, "info",
                        t("Complete the lead: {client}", client=notif.quoted(client_name)),
                        t("The administrator asks for more information."),
                        "leads.html",
                        title_key="ntf_lead_needs_info_title",
                        body_key="ntf_lead_needs_info_body",
                        params={"client": client_name},
                    )
                elif is_win_transition:
                    comm = result.get("commission") or 0
                    notif.create_notification(
                        partner_id, "success",
                        t("Sale won: {client}", client=notif.quoted(client_name)),
                        t("Commission to approve: {amount} PLN", amount=f"{comm:,.0f}"),
                        "earnings.html",
                        title_key="ntf_sale_won_title",
                        body_key="ntf_sale_won_body",
                        params={"client": client_name, "amount": f"{comm:,.0f}"},
                    )
                elif is_lose_transition:
                    notif.create_notification(
                        partner_id, "info",
                        t("Sale lost: {client}", client=notif.quoted(client_name)),
                        t("The lead was closed without a sale."),
                        "leads.html",
                        title_key="ntf_sale_lost_title",
                        body_key="ntf_sale_lost_body",
                        params={"client": client_name},
                    )

            def send_mail():
                result = ctx.result
                # Email partner on a decision or a closed sale
                notify_by_mail = registration_changed or is_win_transition or is_lose_transition
                if notify_by_mail:
                    p_conn = get_conn()
                    p_row  = p_conn.execute("SELECT email, name FROM users WHERE id=?", (partner_id,)).fetchone()
                    p_conn.close()
                    if p_row:
                        mailer.send_lead_status_changed(
                            p_row["email"], p_row["name"],
                            lead_id, client_name,
                            new_registration if registration_changed else data.get("status"),
                            result.get("commission")
                        )

            ctx.after_commit(record_audit)
            ctx.after_commit(notify)
            ctx.after_commit(send_mail)

        return ctx.result
    finally:
        conn.close()


# ─── REGISTRATION BEFORE THE FUNNEL ────────────────
#
# A submitted deal waits on the registration axis with no sales stage at all.
# The producer has three moves; only accepting puts the deal into the pipeline.
# A partner answering a request for information supplies text and nothing else
# --- they never decide their own lead.
#
# These are named endpoints rather than more values on the generic PATCH,
# because each one is a decision with a message, a notification and an audit
# trail attached, and "PATCH a column" cannot express that a reason is
# mandatory.

REGISTRATION_ACTIONS = ("accept", "reject", "request_info", "undo_reject")

# The registration value each action lands on.
_ACTION_RESULT = {
    "accept": "accepted",
    "reject": "rejected",
    "request_info": "needs_info",
    # Undoing a refusal puts the lead back where it was before the producer
    # touched it -- awaiting a decision. Not "accepted": a mistaken reject is
    # not evidence that the deal is good, only that it was not judged yet.
    "undo_reject": "pending",
}

# Accepting is the only decision that needs no words. Refusing a deal or
# asking for more work always owes the partner a reason.
_ACTIONS_REQUIRING_MESSAGE = ("reject", "request_info")

# How a legacy `registration_status` write maps onto the canonical action.
# `pending` is absent on purpose: un-deciding a lead is the `undo_reject`
# action, which carries its own guard (only a REJECTED lead may be undone).
# Routing a bare PATCH to it would let any status write reach that door
# unchecked, so PATCH still has no way back to pending.
_REGISTRATION_STATUS_TO_ACTION = {
    "accepted": "accept",
    "rejected": "reject",
    "needs_info": "request_info",
}

# Counted in Unicode code points, which is what a person means by "4000
# characters". len() on a Python str already counts code points; the trap is
# doing this check in JavaScript, where it would count UTF-16 units and refuse
# 2000 emoji.
MAX_WORKFLOW_MESSAGE = 4000


class LeadFieldsChanged(Exception):
    """
    The submission was written against a stale set of questions.

    Its own type rather than a ValueError: the answer is "look again", not
    "you typed something wrong", and the handler maps it to 409 so the form
    refetches instead of showing a validation error the partner cannot fix.
    """


class LeadWorkflowConflict(Exception):
    """
    The request was well-formed and permitted, but the lead is not in a state
    where it can happen. Handlers map this to 409 and the UI refetches.

    `code` is a stable string the frontend can branch on without matching
    Polish prose.
    """

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def _workflow_message(value, field, required):
    """Validate one piece of partner-visible workflow text."""
    if not required and (value is None or value == ""):
        return None
    return parse_required_string(value, field, max_length=MAX_WORKFLOW_MESSAGE)


def _workflow_metadata(expected_revision, operation_id):
    """
    Both are mandatory on every workflow write, and neither is ever supplied
    by the server on a caller's behalf: filling in a missing revision would
    quietly turn compare-and-swap back into last-write-wins, which is the
    failure this whole contract exists to prevent.
    """
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
        raise ValueError(t("Field 'expected_revision' is required."))
    if expected_revision < 0:
        raise ValueError(t("Field 'expected_revision' has an invalid value."))
    # A bounded (1..128 char) ASCII string. Case
    # folding it or accepting non-ASCII would let two clients construct
    # different-looking ids that collide (or the same id that silently
    # doesn't), which is exactly the ambiguity a replay key exists to avoid
    # -- comparison stays Python's plain string equality, so it is already
    # case-sensitive without extra code.
    operation_id = parse_required_string(operation_id, "operation_id", max_length=128)
    if not operation_id.isascii():
        raise ValueError(t("Field 'operation_id' may contain ASCII characters only."))
    return expected_revision, operation_id


def _read_lead_for_result(conn, lead_id):
    row = conn.execute(
        """SELECT l.*, u.name AS partner_name, u.company AS partner_company,
                  COALESCE(p.name_en, '') AS product_en
             FROM leads l
             JOIN users u ON u.id = l.partner_id
             LEFT JOIN products p ON p.id = l.product_id
            WHERE l.id = ?""",
        (lead_id,),
    ).fetchone()
    return with_commission_projection(dict(row))


def _add_public_comment(conn, lead_id, author_id, text):
    """The decision's own words, attributed to whoever actually wrote them.

    Returns the comment id, which the caller records in the workflow event:
    that link is what keeps the comment from being deleted afterwards.
    """
    cursor = conn.execute(
        """INSERT INTO lead_comments (lead_id, user_id, body, is_internal, created_at)
           VALUES (?, ?, ?, 0, ?)""",
        (lead_id, author_id, text, datetime.utcnow().isoformat()),
    )
    return cursor.lastrowid


def _workflow_payload_hash(lead_id, action, message, revision):
    payload = json.dumps([lead_id, action, message, revision],
                         ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _authorize_workflow_actor(conn, user, row, *, partner=False):
    if not user:
        raise PermissionError(t("Access denied."))
    actor = conn.execute("SELECT id, role, active FROM users WHERE id=?",
                         (user.get("id"),)).fetchone()
    if actor is None or not actor["active"] or actor["role"] != user.get("role"):
        raise PermissionError(t("Access denied."))
    if partner:
        if actor["role"] != "partner":
            # A vendor on a partner-only action: "belongs to another partner"
            # told him the lead belonged to someone else.
            raise PermissionError(t("This action is for the partner. The vendor changes the"
                                    " stage in the lead details."))
        if row["partner_id"] != actor["id"]:
            raise PermissionError(t("This submission belongs to another partner."))
    elif not is_staff(dict(actor)):
        raise PermissionError(t("The vendor decides on the submission."))


def _check_registration_action(row, action):
    """
    Refuse a registration decision the lead's current state does not allow.

    Shared by the write and its preview, so a proposal that the write would
    refuse is refused before anyone is asked to confirm it. Returns the first
    pipeline stage when accepting (the stage an accepted deal starts on).
    """
    new_registration = _ACTION_RESULT[action]
    current = row["registration_status"]
    # Rejecting an already-accepted, already-closed deal would take it off a
    # terminal stage (`status = NULL`), which only the reopen action may do.
    # An accepted deal still on a nonterminal stage may still be rejected.
    current_stage = lead_stages.get_by_key(row["status"]) if row["status"] else None
    if action == "reject" and current_stage and current_stage["is_terminal"]:
        raise LeadWorkflowConflict(
            t("The lead is read-only. The vendor can reopen it."),
            "lead_read_only",
        )
    if current == new_registration and action != "request_info":
        raise LeadWorkflowConflict(
            t("The lead is already in the state: {state}.",
              state=t(REGISTRATION_LABELS[new_registration])),
            "already_in_state",
        )
    # Only a refusal can be taken back, and only while it is still the
    # lead's current state. Anything else is a fresh decision.
    if action == "undo_reject" and current != "rejected":
        raise LeadWorkflowConflict(
            t("Only a rejection can be undone."),
            "not_rejected",
        )
    if action == "request_info" and current != "pending":
        raise LeadWorkflowConflict(
            t("More information can be requested only before the decision."),
            "not_pending",
        )
    if action == "accept":
        # Accepting is what puts the deal into the sale. It starts at the
        # first stage the producer configured, read live.
        first_stage = lead_stages.get_first_pipeline_stage()
        if not first_stage:
            raise LeadWorkflowConflict(
                t("There is no active sales stage. Set up the pipeline before accepting leads."),
                "no_pipeline_stage",
            )
        return first_stage
    return None


def preview_registration_action(lead_id, user, action, message=None):
    """
    Validate a registration decision against the lead's current state
    without writing, and return what apply_registration_action would do.

    The snapshot carries the revision the decision was judged against, so
    the confirmed write can refuse to land on a lead that changed meanwhile.
    """
    lead_id = _parse_lead_id(lead_id)
    action = parse_enum(action, "action", REGISTRATION_ACTIONS)
    text = _workflow_message(
        message, N_("Decision message"), required=action in _ACTIONS_REQUIRING_MESSAGE
    )
    if not is_staff(user):
        raise PermissionError(t("The vendor decides on the submission."))

    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if row is None:
            raise LeadNotFoundError(t("Lead {lead} does not exist.", lead=lead_id))
        _check_registration_action(row, action)
        return {
            "data": {"action": action, "message": text},
            "snapshot": {
                "revision": row["revision"],
                "updated_at": row["updated_at"],
                "status": row["status"],
                "registration_status": row["registration_status"],
            },
            "preview": {
                "lead_id": lead_id,
                "client_name": row["client_name"],
                "old_registration_status": row["registration_status"],
                "new_registration_status": _ACTION_RESULT[action],
                "message": text,
            },
        }
    finally:
        conn.close()


def apply_registration_action(lead_id, user, action, message=None,
                              expected_revision=None, operation_id=None):
    """
    Accept, reject, or ask for more information about a submitted lead.

    One transaction covers the decision, its partner-visible message and the
    version bump, so a lead can never end up reading "rejected" with the
    reason missing.
    """
    lead_id = _parse_lead_id(lead_id)
    action = parse_enum(action, "action", REGISTRATION_ACTIONS)
    text = _workflow_message(
        message, N_("Decision message"), required=action in _ACTIONS_REQUIRING_MESSAGE
    )
    expected_revision, operation_id = _workflow_metadata(expected_revision, operation_id)

    if not is_staff(user):
        raise PermissionError(t("The vendor decides on the submission."))

    new_registration = _ACTION_RESULT[action]

    conn = get_conn()
    try:
        with lead_workflow_transaction(
            conn, lead_id, actor_id=user["id"],
            expected_revision=expected_revision, operation_id=operation_id,
            payload_hash=_workflow_payload_hash(lead_id, action, text, expected_revision),
            authorize=lambda row: _authorize_workflow_actor(conn, user, row),
        ) as ctx:
            if ctx.replayed:
                return ctx.result
            current = ctx.row["registration_status"]
            first_stage = _check_registration_action(ctx.row, action)

            values = {
                "registration_status": new_registration,
                "updated_at": datetime.utcnow().isoformat(),
            }
            if action == "accept":
                if not ctx.row["status"]:
                    values["status"] = first_stage["key"]
            else:
                # A deal that is not approved is not anywhere in the sale, so
                # it must not sit on a stage that would keep counting into
                # pipeline value.
                values["status"] = None

            ctx.update(values)
            comment_id = _add_public_comment(conn, lead_id, user["id"], text) if text else None
            ctx.record_event(f"registration.{action}", {
                "from": current, "to": new_registration, "has_message": bool(text),
                "comment_id": comment_id,
            })
            ctx.result_builder = lambda: _read_lead_for_result(conn, lead_id)

            partner_id = ctx.row["partner_id"]
            client_name = ctx.row["client_name"]
            ctx.after_commit(lambda: _notify_registration(
                partner_id, client_name, action, lead_id
            ))
            # The partner is told by mail as well as in-app, exactly as the
            # legacy staff update did. Kept here rather than left to the
            # caller so every route to a decision -- named endpoint, PATCH,
            # bulk -- sends the same message.
            ctx.after_commit(lambda: _email_registration_decision(
                partner_id, lead_id, client_name, new_registration
            ))
            ctx.after_commit(lambda: audit.log(
                user, f"lead.{action}", "lead", lead_id,
                f"{client_name} → {t(REGISTRATION_LABELS[new_registration])}",
            ))

        return ctx.result
    finally:
        conn.close()


def _email_registration_decision(partner_id, lead_id, client_name, new_registration):
    """Sent after commit; a failing mailer never unsays a saved decision."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT email, name FROM users WHERE id = ?", (partner_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return
    conn = get_conn()
    try:
        commission = conn.execute(
            "SELECT commission FROM leads WHERE id = ?", (lead_id,)
        ).fetchone()
    finally:
        conn.close()
    mailer.send_lead_status_changed(
        row["email"], row["name"], lead_id, client_name,
        new_registration, commission["commission"] if commission else None,
    )


# One shape per decision: the sentence as stored text (in the actor's
# language), and the same sentence as dictionary keys so the partner reads it
# in his own language.
_REGISTRATION_NOTICES = {
    "accept": ("success", N_("Lead accepted: {client}"),
               N_("The submission moved to sales."),
               "ntf_bulk_accepted_title", "ntf_bulk_accepted_body"),
    "reject": ("error", N_("Lead rejected: {client}"),
               N_("You will find the reason in the submission's comments."),
               "ntf_bulk_rejected_title", "ntf_bulk_rejected_body"),
    "undo_reject": ("info", N_("Rejection undone: {client}"),
                    N_("The submission is back under review."),
                    "ntf_undo_reject_title", "ntf_undo_reject_body"),
}
_REGISTRATION_NOTICE_DEFAULT = (
    "info", N_("Complete the lead: {client}"), N_("The vendor asks for more information."),
    "ntf_bulk_needs_info_title", "ntf_bulk_needs_info_body",
)


def _notify_registration(partner_id, client_name, action, lead_id):
    """Told after the write is durable, never inside it."""
    type_, title, body, title_key, body_key = _REGISTRATION_NOTICES.get(
        action, _REGISTRATION_NOTICE_DEFAULT
    )
    notif.create_notification(
        partner_id, type_, t(title, client=notif.quoted(client_name)), t(body), "leads.html",
        title_key=title_key, body_key=body_key, params={"client": client_name})


def _partner_details_editable(conn, row):
    stage = conn.execute("SELECT * FROM lead_stages WHERE key=?", (row["status"],)).fetchone()
    # Financial evidence and any terminal stage fail closed, including a
    # malformed registration/stage combination inherited from older data.
    if row["won_at"] or (stage and (stage["is_terminal"] or stage["kind"] in ("win", "lose"))):
        return False
    if row["registration_status"] in ("pending", "needs_info"):
        return row["status"] is None
    return bool(row["registration_status"] == "accepted" and stage
                and stage["active"] and stage["kind"] == "progress")


def _details_result(conn, lead_id, user):
    lead = _read_lead_for_result(conn, lead_id)
    stage = conn.execute("SELECT name FROM lead_stages WHERE key=?", (lead["status"],)).fetchone()
    lead["stage_name"] = stage["name"] if stage else None
    editable = user.get("role") == "partner" and lead["partner_id"] == user["id"]
    lead["permissions"] = {"edit_details": bool(editable and _partner_details_editable(conn, lead))}
    lead["field_values"] = lead_fields.get_values(lead_id, conn=conn)
    lead["field_schema"] = lead_fields.list_fields(conn=conn)
    lead["schema_revision"] = lead_fields.schema_version(conn=conn)
    return lead


def update_partner_lead_details(lead_id, user, data):
    """Owner-only contact and custom-field patch, using one locked snapshot
    for permissions, schema, values, revision, history and replay result."""
    if not user or user.get("role") != "partner":
        raise PermissionError(t("Only the owner of the submission can edit the details."))
    lead_id = _parse_lead_id(lead_id)
    values = parse_allowed_fields(data, ("client_name", "client_email", "client_phone", "notes",
        "lead_field_values", "expected_revision", "schema_revision", "operation_id"))
    revision, operation_id = _workflow_metadata(values.pop("expected_revision", None),
                                               values.pop("operation_id", None))
    schema = parse_required_string(values.pop("schema_revision", None), "schema_revision", 64)
    if not values:
        raise ValueError(t("No data to update."))
    # Stable key order: JSON object ordering is not an operation identity.
    payload_hash = _workflow_payload_hash(lead_id, "details.update",
        json.dumps([schema, values], sort_keys=True, ensure_ascii=False), revision)
    changes = {}
    for key, raw in values.items():
        if key == "client_name":
            changes[key] = parse_required_string(raw, key, 200)
        elif key == "client_email":
            changes[key] = parse_email(raw, key)
        elif key in ("notes", "client_phone"):
            changes[key] = parse_optional_string(raw, key, 5000 if key == "notes" else 50)
    conn = get_conn()
    try:
        with lead_workflow_transaction(conn, lead_id, actor_id=user["id"],
            expected_revision=revision, operation_id=operation_id, payload_hash=payload_hash,
            authorize=lambda row: _authorize_workflow_actor(conn, user, row, partner=True)) as ctx:
            if ctx.replayed:
                return ctx.result
            if schema != lead_fields.schema_version(conn=conn):
                raise LeadWorkflowConflict(t("The vendor changed the available fields. "
                                             "Refresh the form."), "stale_lead_fields")
            if not _partner_details_editable(conn, ctx.row):
                raise LeadWorkflowConflict(t("The lead is read-only."), "lead_read_only")
            prepared, cleared = lead_fields.validate_partner_patch(conn, values.get("lead_field_values", {}))
            changes["updated_at"] = datetime.utcnow().isoformat()
            ctx.update(changes)
            lead_fields.save_values_in_transaction(conn, lead_id, prepared)
            for field_id in cleared:
                conn.execute("DELETE FROM lead_field_values WHERE lead_id=? AND field_id=?", (lead_id, field_id))
            ctx.record_event("details.update", {"fields": sorted(values),
                "custom_field_ids": sorted(set(prepared) | set(cleared))})
            ctx.result_builder = lambda: _details_result(conn, lead_id, user)
        return ctx.result
    finally:
        conn.close()


def handle_details(handler, user, lead_id, body):
    try:
        return 200, {"lead": update_partner_lead_details(lead_id, user, body)}
    except (PermissionError, ValueError, StaleRevisionError, OperationConflictError,
            LeadWorkflowConflict) as exc:
        return _workflow_error_response(exc)


# ─── PARTNER KANBAN: OWNER-SCOPED STAGE MOVE ───
#
# The partner's own board move. Forward and backward between the producer's
# configured nonterminal stages is unrestricted -- there is no notion of
# "skipping a step" here, only "which active stage is this deal on now".
# Entering a terminal stage (won/lost) requires the caller to say so
# explicitly with confirmed_terminal=True: the confirmation popup the UI
# shows before sending this is not itself the security boundary, this flag
# is -- a forged request that skips the popup still needs it set.
# Commission/won_at are computed with the exact same rules update_lead uses
# for the admin PATCH (_commission_projection); the partner never supplies a
# commission amount or won_at directly.

_STAGE_MOVE_FIELDS = ("target_stage", "expected_revision", "operation_id", "confirmed_terminal")


def move_partner_lead(lead_id, user, data):
    """Move the caller's own accepted lead between configured sales stages.

    Same source and destination is a true no-op: no revision bump, no
    history event, no notification. A destination that is not a currently
    active configured stage is rejected as `unknown_stage` -- the UI never
    auto-relocates a lead sitting on a stage that was renamed or removed
    from under it.
    """
    lead_id = _parse_lead_id(lead_id)
    values = parse_allowed_fields(data, _STAGE_MOVE_FIELDS)
    target_stage_key = parse_required_string(values.get("target_stage"), "target_stage", max_length=64)
    confirmed_terminal = bool(
        parse_boolean_flag(values.get("confirmed_terminal", False), "confirmed_terminal")
    )
    revision, operation_id = _workflow_metadata(
        values.pop("expected_revision", None), values.pop("operation_id", None)
    )
    payload_hash = _workflow_payload_hash(
        lead_id, "stage.move",
        json.dumps([target_stage_key, confirmed_terminal], sort_keys=True, ensure_ascii=False),
        revision,
    )

    conn = get_conn()
    try:
        with lead_workflow_transaction(
            conn, lead_id, actor_id=user["id"],
            expected_revision=revision, operation_id=operation_id, payload_hash=payload_hash,
            authorize=lambda row: _authorize_workflow_actor(conn, user, row, partner=True),
        ) as ctx:
            if ctx.replayed:
                return ctx.result
            row = ctx.row
            if row["registration_status"] != "accepted":
                raise LeadWorkflowConflict(
                    t("The lead is not accepted yet."), "not_accepted"
                )
            if row["status"] is None:
                raise LeadWorkflowConflict(
                    t("There is no active sales stage for this lead."), "no_active_stage"
                )
            current_stage = lead_stages.get_by_key(row["status"])
            if current_stage is None:
                # A stage this lead is actually sitting on, but that no longer
                # exists/is active in the producer's configuration -- the
                # partner UI shows this in its own "Stage not available" section,
                # never as a movable board column.
                raise LeadWorkflowConflict(
                    t("The stage is not available. Refresh the data or contact the vendor."),
                    "unknown_stage",
                )
            if current_stage["is_terminal"]:
                raise LeadWorkflowConflict(
                    t("The lead is closed and read-only. The vendor can reopen it."),
                    "lead_read_only",
                )
            target_stage = lead_stages.get_by_key(target_stage_key)
            if not target_stage or not target_stage["active"]:
                raise LeadWorkflowConflict(
                    t("The stage is not available. Refresh the data or contact the vendor."),
                    "unknown_stage",
                )

            if target_stage["key"] == row["status"]:
                return _details_result(conn, lead_id, user)

            is_win_transition = target_stage["kind"] == "win"
            is_lose_transition = target_stage["kind"] == "lose"
            if target_stage["is_terminal"] and not confirmed_terminal:
                raise LeadWorkflowConflict(
                    t("Closing the lead needs a confirmation."), "confirmation_required"
                )

            commission_projection = _commission_projection(row, {}, target_stage)
            updates = {"status": target_stage["key"], "updated_at": datetime.utcnow().isoformat()}
            if is_win_transition and row["won_at"] is None:
                updates["won_at"] = datetime.utcnow().isoformat()
                # See update_lead's identical branch: a fresh win cycle
                # starts 'unpaid', an explicit bounded assumption.
                updates["settlement_status"] = _initial_win_settlement(row)
            if commission_projection["calculated"]:
                updates["commission"] = commission_projection["commission"]

            ctx.update(updates)
            ctx.record_event("stage.move", {
                "from": row["status"], "to": target_stage["key"],
                "confirmed_terminal": confirmed_terminal,
            })
            ctx.result_builder = lambda: _details_result(conn, lead_id, user)

            partner_id = row["partner_id"]
            client_name = row["client_name"]
            if is_win_transition:
                ctx.after_commit(lambda: notif.create_notification(
                    partner_id, "success", t("Sale won: {client}", client=notif.quoted(client_name)),
                    t("Commission to approve: {amount} PLN",
                      amount=f"{(ctx.result.get('commission') or 0):,.0f}"),
                    "earnings.html",
                    title_key="ntf_sale_won_title", body_key="ntf_sale_won_body",
                    params={"client": client_name,
                            "amount": f"{(ctx.result.get('commission') or 0):,.0f}"}))
            elif is_lose_transition:
                ctx.after_commit(lambda: notif.create_notification(
                    partner_id, "info", t("Sale lost: {client}", client=notif.quoted(client_name)),
                    t("The lead was closed without a sale."), "leads.html",
                    title_key="ntf_sale_lost_title", body_key="ntf_sale_lost_body",
                    params={"client": client_name}))

            audit_action = (
                f"lead.{target_stage['key']}" if (is_win_transition or is_lose_transition)
                else "lead.stage_move"
            )
            ctx.after_commit(lambda: audit.log(
                user, audit_action, "lead", lead_id,
                f"{client_name} → {target_stage['name']}"))

            if is_win_transition or is_lose_transition:
                def _mail_stage_change():
                    p_conn = get_conn()
                    try:
                        p_row = p_conn.execute(
                            "SELECT email, name FROM users WHERE id=?", (partner_id,)
                        ).fetchone()
                    finally:
                        p_conn.close()
                    if p_row:
                        mailer.send_lead_status_changed(
                            p_row["email"], p_row["name"], lead_id, client_name,
                            target_stage["key"], ctx.result.get("commission"))
                ctx.after_commit(_mail_stage_change)

        return ctx.result
    finally:
        conn.close()


def handle_move_stage(handler, user, lead_id, body):
    try:
        return 200, {"lead": move_partner_lead(lead_id, user, body)}
    except (PermissionError, ValueError, StaleRevisionError, OperationConflictError,
            LeadWorkflowConflict) as exc:
        return _workflow_error_response(exc)


# ─── REOPENING A CLOSED LEAD ────────────────
#
# The shared `_terminal_exit_attempted` guard blocks every OTHER caller
# of `update_lead`/`apply_registration_action` from moving a won/lost lead's
# pipeline position -- the generic admin PATCH, the AI-confirmed action, a
# "reject" registration decision. This is the one deliberate, audited door
# that guard was built to make necessary: a producer admin, and only a
# producer admin, pulling a mistakenly closed deal back into an editable
# nonterminal stage. It does not call update_lead and is not reachable
# through it -- a forged field on the generic PATCH still hits the guard;
# only this dedicated action, under its own capability check, bypasses it.
#
# The money question this whole feature exists to answer -- "was the
# recorded commission actually paid?" -- has no reliable answer from the
# commission record alone. `settlement_status` (unknown/unpaid/paid,
# api/db.py's migration) is a minimal, producer-reviewed, audited fact about
# ONE lead's current win cycle, not a payout engine: it never executes or
# reverses a payment, and 'paid' has no ordinary path back to 'unpaid'.

SETTLEMENT_REVIEW_RESULTS = ("unpaid", "paid")

_REOPEN_FIELDS = ("target_stage", "reason", "confirmed", "expected_revision", "operation_id")
_REVIEW_ELIGIBILITY_FIELDS = ("result", "reason", "confirmed", "expected_revision", "operation_id")


def _authorize_reopen_actor(conn, user, row):
    """Re-verify the capability against the DB row under the transaction's
    own lock, not just the session snapshot the caller arrived with --
    mirrors _authorize_workflow_actor's defense-in-depth shape for the
    partner-owned actions, keyed to can_reopen_lead instead of is_staff."""
    if not user:
        raise PermissionError(t("Access denied."))
    actor = conn.execute(
        "SELECT id, role, active FROM users WHERE id=?", (user.get("id"),)
    ).fetchone()
    if actor is None or not actor["active"] or actor["role"] != user.get("role"):
        raise PermissionError(t("Access denied."))
    if not can_reopen_lead(dict(actor)):
        raise PermissionError(t("Only the vendor's administrator can reopen the lead."))


def _initial_win_settlement(row):
    """Only genuinely new earnings start unpaid. A missing win date does
    not invalidate existing settlement evidence or a recorded zero."""
    if row["settlement_status"] is not None:
        return row["settlement_status"]
    if (row["commission"] is not None or row["settlement_reviewed_at"]
            or row["settlement_reviewed_by"] is not None):
        return "unknown"
    return "unpaid"


def _has_reopen_financial_history(row):
    return bool(row["commission"] is not None or row["won_at"]
                or row["settlement_status"] is not None
                or row["settlement_reviewed_by"] is not None
                or row["settlement_reviewed_at"])


def _reopen_blocked_reason(row):
    """None when a terminal lead's financial state permits an ordinary
    reopen; otherwise the LeadWorkflowConflict code explaining why not.

    Only a lead without financial evidence needs no review. A missing
    commission amount cannot invalidate a win timestamp or settlement review.
    Recorded financial evidence gates on the producer's own
    settlement_status: only an explicit 'unpaid' review passes; 'unknown'
    (never assumed, including every legacy row) and 'paid' (immutable for
    this cycle) both block.
    """
    if row["commission_paid_at"]:
        return "settlement_paid"
    if not _has_reopen_financial_history(row):
        return None
    status = row["settlement_status"]
    if status == "unpaid":
        return None
    if status == "paid":
        return "settlement_paid"
    return "settlement_unknown"


def reopen_lead(lead_id, user, target_stage, reason=None, confirmed=False,
                expected_revision=None, operation_id=None):
    """
    Producer admin only: pull a mistakenly won/lost lead back into an
    editable, active, nonterminal stage the producer names explicitly.

    Requires an accepted terminal source, an active nonterminal destination,
    and a reason -- `confirmed=True` is the explicit, forgeable-request-proof
    boundary (mirrors move_partner_lead's own confirmed_terminal):
    the confirmation popup the UI shows is not itself the security boundary.
    Financial history (a recorded commission) is gated on a producer's own
    settlement review, never inferred from the amount or the win date.
    """
    lead_id = _parse_lead_id(lead_id)
    if not can_reopen_lead(user):
        raise PermissionError(t("Only the vendor's administrator can reopen the lead."))
    target_stage_key = parse_required_string(target_stage, "target_stage", max_length=64)
    reason = _workflow_message(reason, N_("Reopening reason"), required=True)
    if parse_boolean_flag(confirmed, "confirmed") != 1:
        raise ValueError(t("Confirmation of the reopening is required."))
    expected_revision, operation_id = _workflow_metadata(expected_revision, operation_id)
    payload_hash = _workflow_payload_hash(
        lead_id, "lead.reopen",
        json.dumps([target_stage_key, reason], sort_keys=True, ensure_ascii=False),
        expected_revision,
    )

    conn = get_conn()
    try:
        with lead_workflow_transaction(
            conn, lead_id, actor_id=user["id"],
            expected_revision=expected_revision, operation_id=operation_id,
            payload_hash=payload_hash,
            authorize=lambda row: _authorize_reopen_actor(conn, user, row),
        ) as ctx:
            if ctx.replayed:
                return ctx.result
            row = ctx.row
            if row["registration_status"] != "accepted":
                raise LeadWorkflowConflict(
                    t("Reopening needs an accepted submission."), "not_accepted")
            current_stage = lead_stages.get_by_key(row["status"]) if row["status"] else None
            if not current_stage or not current_stage["is_terminal"]:
                raise LeadWorkflowConflict(
                    t("The lead is not closed, so there is nothing to reopen."),
                    "not_terminal",
                )
            target_stage_row = lead_stages.get_by_key(target_stage_key)
            if not target_stage_row or not target_stage_row["active"]:
                raise LeadWorkflowConflict(
                    t("The stage is not available. Refresh the data or contact the vendor."),
                    "unknown_stage",
                )
            if target_stage_row["is_terminal"]:
                raise LeadWorkflowConflict(
                    t("The target stage must be an active, non-terminal stage."),
                    "target_not_nonterminal",
                )

            blocked_code = _reopen_blocked_reason(row)
            if blocked_code == "settlement_unknown":
                raise LeadWorkflowConflict(
                    t("The commission settlement has not been confirmed. Before reopening, "
                      "check the settlements and record the verification result."),
                    "settlement_unknown",
                )
            if blocked_code == "settlement_paid":
                raise LeadWorkflowConflict(
                    t("The lead cannot be reopened: the commission has already been settled"
                      " or paid out. Contact the person responsible for settlements to "
                      "agree a correction."),
                    "settlement_paid",
                )

            prior_kind = current_stage["kind"]
            # Legacy lost leads may retain a past win date. Once reviewed and
            # reopened, that timestamp belongs only to history: leaving it
            # active would keep the partner editor locked after a valid reopen.
            updates = {"status": target_stage_key, "won_at": None,
                       "updated_at": datetime.utcnow().isoformat()}
            # Only an unpaid WIN's active accrual is removed from payable
            # totals. A lost
            # lead's rarer, manually-set commission is gated by the exact
            # same settlement review above, but reopening does not zero it
            # out on reopen -- commission_override_pct is retained either
            # way, so the negotiated rate is never lost.
            if prior_kind == "win":
                updates.update({
                    "won_at": None,
                    "commission": None,
                    "settlement_status": None,
                    "settlement_reviewed_by": None,
                    "settlement_reviewed_at": None,
                    "settlement_review_reason": None,
                })
            ctx.update(updates)
            ctx.record_event("lead.reopen", {
                "from": row["status"], "to": target_stage_key,
                "prior_kind": prior_kind,
                "prior_commission": row["commission"],
                "prior_won_at": row["won_at"],
                "prior_settlement_status": row["settlement_status"],
                "prior_settlement_reviewed_by": row["settlement_reviewed_by"],
                "prior_settlement_reviewed_at": row["settlement_reviewed_at"],
                "prior_settlement_review_reason": row["settlement_review_reason"],
                "reason": reason,
            })
            ctx.result_builder = lambda: _read_lead_for_result(conn, lead_id)

            partner_id = row["partner_id"]
            client_name = row["client_name"]
            target_name = target_stage_row["name"]
            ctx.after_commit(lambda: notif.create_notification(
                partner_id, "info", t("Lead reopened: {client}", client=notif.quoted(client_name)),
                t("The vendor reopened this lead for editing."), "leads.html",
                title_key="ntf_lead_reopened_title", body_key="ntf_lead_reopened_body",
                params={"client": client_name}))
            ctx.after_commit(lambda: audit.log(
                user, "lead.reopen", "lead", lead_id,
                f"{client_name} → {target_name} | {reason}"))

            def _mail_reopened():
                p_conn = get_conn()
                try:
                    p_row = p_conn.execute(
                        "SELECT email, name FROM users WHERE id=?", (partner_id,)
                    ).fetchone()
                finally:
                    p_conn.close()
                if p_row:
                    mailer.send_lead_status_changed(
                        p_row["email"], p_row["name"], lead_id, client_name,
                        target_stage_key, None)
            ctx.after_commit(_mail_reopened)

        return ctx.result
    finally:
        conn.close()


def review_reopen_eligibility(lead_id, user, result, reason=None, confirmed=False,
                              expected_revision=None, operation_id=None):
    """
    Producer admin only: record what is actually known about a terminal
    lead's recorded commission -- 'unpaid' (confirmed not settled) or 'paid'
    (confirmed settled/paid). Neither value is ever inferred from the
    commission's amount or won_at's date; both require the producer's own
    conscious verification, audited with who/when/why.

    Saving a review moves nothing by itself -- only a saved 'unpaid' result
    subsequently lets reopen_lead succeed; the reopen itself is still a
    separate, explicit action. 'paid' is immutable for the lead's current
    win cycle: once set, this call refuses again until the next win/reopen
    starts a fresh cycle. A correction to an already-paid record is a
    deliberately separate, not-yet-built workflow, never a flip back
    to 'unpaid' here.
    """
    lead_id = _parse_lead_id(lead_id)
    if not can_reopen_lead(user):
        raise PermissionError(t("Only the vendor's administrator can verify the settlement."))
    result = parse_enum(result, "result", SETTLEMENT_REVIEW_RESULTS)
    reason = _workflow_message(reason, N_("Verification rationale"), required=True)
    if parse_boolean_flag(confirmed, "confirmed") != 1:
        raise ValueError(t("Confirmation of the verification result is required."))
    expected_revision, operation_id = _workflow_metadata(expected_revision, operation_id)
    payload_hash = _workflow_payload_hash(
        lead_id, "lead.reopen_eligibility",
        json.dumps([result, reason], sort_keys=True, ensure_ascii=False),
        expected_revision,
    )

    conn = get_conn()
    try:
        with lead_workflow_transaction(
            conn, lead_id, actor_id=user["id"],
            expected_revision=expected_revision, operation_id=operation_id,
            payload_hash=payload_hash,
            authorize=lambda row: _authorize_reopen_actor(conn, user, row),
        ) as ctx:
            if ctx.replayed:
                return ctx.result
            row = ctx.row
            current_stage = lead_stages.get_by_key(row["status"]) if row["status"] else None
            if not current_stage or not current_stage["is_terminal"]:
                raise LeadWorkflowConflict(
                    t("The lead is not closed, so a settlement verification does not apply."),
                    "not_terminal",
                )
            if not _has_reopen_financial_history(row):
                raise LeadWorkflowConflict(
                    t("This lead has no recorded commission, so no settlement verification "
                      "is needed."),
                    "no_financial_history",
                )
            if row["settlement_status"] == "paid" or row["commission_paid_at"]:
                raise LeadWorkflowConflict(
                    t("The lead cannot be reopened: the commission has already been settled"
                      " or paid out. Contact the person responsible for settlements to "
                      "agree a correction."),
                    "settlement_paid",
                )

            ctx.update({
                "settlement_status": result,
                "settlement_reviewed_by": user["id"],
                "settlement_reviewed_at": datetime.utcnow().isoformat(),
                "settlement_review_reason": reason,
                "updated_at": datetime.utcnow().isoformat(),
            })
            ctx.record_event("lead.reopen_eligibility", {
                "from": row["settlement_status"], "to": result, "reason": reason,
            })
            ctx.result_builder = lambda: _read_lead_for_result(conn, lead_id)

            client_name = row["client_name"]
            ctx.after_commit(lambda: audit.log(
                user, "lead.reopen_eligibility", "lead", lead_id,
                t("{client} settlement → {result} | {reason}",
                  client=client_name, result=result, reason=reason),
                detail_key="audit_detail_settlement_review",
                detail_params={"client": client_name, "result": result,
                               "reason": reason or ""}))

        return ctx.result
    finally:
        conn.close()


def handle_reopen(handler, user, lead_id, body):
    try:
        values = parse_allowed_fields(body, _REOPEN_FIELDS)
        lead = reopen_lead(
            lead_id, user,
            target_stage=values.get("target_stage"),
            reason=values.get("reason"),
            confirmed=values.get("confirmed", False),
            expected_revision=values.get("expected_revision"),
            operation_id=values.get("operation_id"),
        )
        return 200, {"lead": lead}
    except (PermissionError, ValueError, StaleRevisionError, OperationConflictError,
            LeadWorkflowConflict) as exc:
        return _workflow_error_response(exc)


def handle_reopen_eligibility(handler, user, lead_id, body):
    try:
        values = parse_allowed_fields(body, _REVIEW_ELIGIBILITY_FIELDS)
        lead = review_reopen_eligibility(
            lead_id, user,
            result=values.get("result"),
            reason=values.get("reason"),
            confirmed=values.get("confirmed", False),
            expected_revision=values.get("expected_revision"),
            operation_id=values.get("operation_id"),
        )
        return 200, {"lead": lead}
    except (PermissionError, ValueError, StaleRevisionError, OperationConflictError,
            LeadWorkflowConflict) as exc:
        return _workflow_error_response(exc)


def respond_to_information(lead_id, user, message=None,
                           expected_revision=None, operation_id=None):
    """
    The partner's answer to a request for information.

    Answering is not deciding: the lead stays in needs_info and keeps waiting
    for the producer. This is deliberate --- letting a reply auto-accept would
    hand the partner the producer's decision.
    """
    lead_id = _parse_lead_id(lead_id)
    text = _workflow_message(message, N_("Reply message"), required=True)
    expected_revision, operation_id = _workflow_metadata(expected_revision, operation_id)
    if not user or user.get("role") != "partner":
        raise PermissionError(t("Only the owner of the submission can send a reply."))

    conn = get_conn()
    try:
        with lead_workflow_transaction(
            conn, lead_id, actor_id=user["id"],
            expected_revision=expected_revision, operation_id=operation_id,
            payload_hash=_workflow_payload_hash(lead_id, "information.response", text, expected_revision),
            authorize=lambda row: _authorize_workflow_actor(conn, user, row, partner=True),
        ) as ctx:
            if ctx.replayed:
                return ctx.result
            # Ownership is settled here, in the domain, rather than trusted
            # from whichever list produced the id -- editing the URL is the
            # first thing anyone tries.
            if ctx.row["partner_id"] != user["id"]:
                raise PermissionError(t("This submission belongs to another partner."))
            if ctx.row["registration_status"] != "needs_info":
                raise LeadWorkflowConflict(
                    t("The vendor did not ask for more information on this submission."),
                    "not_awaiting_information",
                )

            ctx.update({"updated_at": datetime.utcnow().isoformat()})
            comment_id = _add_public_comment(conn, lead_id, user["id"], text)
            ctx.record_event("information.response", {
                "length": len(text), "comment_id": comment_id,
            })
            ctx.result_builder = lambda: _read_lead_for_result(conn, lead_id)

            client_name = ctx.row["client_name"]
            # A partner context cannot read staff rows (migration 0017).
            reviewers = conn.execute("SELECT id, role FROM active_staff()").fetchall()
            for reviewer in reviewers:
                if is_staff(dict(reviewer)):
                    ctx.after_commit(lambda reviewer_id=reviewer["id"]: notif.create_notification(
                        reviewer_id, "info", t("The partner completed the lead: {client}", client=notif.quoted(client_name)),
                        t("The submission is waiting for a decision."), "leads.html",
                        title_key="ntf_partner_answered_title",
                        body_key="ntf_partner_answered_body",
                        params={"client": client_name}))

        return ctx.result
    finally:
        conn.close()


# ─── HTTP HANDLERS ─────────────────────────────────────────────────────────────

def handle_list(handler, user: dict, qs: dict):
    # Explicit rather than implicit: an unrecognised role is refused here
    # instead of falling through to get_leads, whose partner branch is the
    # only row-scoping in the query. An Observer is admitted (leads are
    # the whole of their access) and refused on every write path, which all
    # go through is_staff().
    if not can_read_leads(user):
        return 403, {"error": t("Access denied.")}

    filters = {
        "status":              qs.get("status",              [None])[0],
        "registration_status": qs.get("registration_status", [None])[0],
        "partner_id": qs.get("partner_id", [None])[0],
        "q":          qs.get("q",          [None])[0],
        "date_from":  qs.get("date_from",  [None])[0],
        "date_to":    qs.get("date_to",    [None])[0],
        "min_value":  _qs_filter(qs, "min_value"),
        "overdue":    _qs_filter(qs, "overdue"),
    }
    sort      = _qs_filter(qs, "sort")
    direction = _qs_filter(qs, "dir")

    # Paging is opt-in. A caller that asks for neither `page` nor `per_page`
    # still gets every match, because two of them need it: the partner's board
    # draws a column per sales stage out of the whole set, and a truncated
    # board reads as lost deals. The producer's list, which is the one that had
    # ten thousand rows to carry, asks for a page.
    if "page" not in qs and "per_page" not in qs:
        try:
            leads = get_leads(user, filters, sort=sort, direction=direction)
        except ValueError as e:
            return 400, {"error": str(e)}
        return 200, {"leads": leads, "total": len(leads)}

    try:
        page     = parse_non_negative_integer(qs.get("page", ["1"])[0] or "1", "page")
        per_page = parse_non_negative_integer(
            qs.get("per_page", [str(LEADS_PAGE_SIZE)])[0] or str(LEADS_PAGE_SIZE),
            "per_page", maximum=LEADS_MAX_PAGE_SIZE,
        )
        leads, total = get_leads_page(
            user, filters, sort=sort, direction=direction,
            page=max(1, page), per_page=max(1, per_page),
        )
    except ValueError as e:
        return 400, {"error": str(e)}

    per_page = max(1, min(per_page or LEADS_PAGE_SIZE, LEADS_MAX_PAGE_SIZE))
    return 200, {
        "leads": leads,
        "total": total,
        "page": max(1, page),
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
    }


def _qs_filter(qs, name):
    """Return a query-string value, treating an empty one as absent.

    `?min_value=` is what a browser sends for a cleared number box, and it
    means "no minimum" -- not "a minimum I failed to write down", which is how
    the number parser would read it.
    """
    value = qs.get(name, [None])[0]
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return value


def handle_get(handler, user: dict, lead_id: str):
    if not can_read_leads(user):
        return 403, {"error": t("Access denied.")}
    lead = get_lead(lead_id, user)
    if lead is None:
        return 404, {"error": t("Lead not found.")}
    # Answers travel with the detail, each carrying its own question -- including
    # questions the producer has since retired, so an old answer never appears
    # as a value with no label.
    conn = get_conn()
    try:
        conn.execute("BEGIN")
        # Recheck ownership within the same read snapshot as the detail.
        row = conn.execute("SELECT * FROM leads WHERE id=?", (lead["id"],)).fetchone()
        if row is None or (user.get("role") == "partner" and row["partner_id"] != user["id"]):
            return 404, {"error": t("Lead not found.")}
        lead = _details_result(conn, lead["id"], user)
    finally:
        conn.close()
    return 200, {"lead": lead}


def handle_create(handler, user: dict, body: dict):
    try:
        lead = submit_lead(user, body)
        return 201, {"lead": lead}
    except PermissionError as e:
        return 403, {"error": str(e)}
    except LeadFieldsChanged as e:
        return 409, {"error": str(e), "code": "stale_lead_fields"}
    except ValueError as e:
        return 400, {"error": str(e)}


def handle_assign(handler, user: dict, body: dict):
    """POST /api/leads/assign -- the producer creates a lead for a partner."""
    if not can_assign_lead(user):
        return 403, {"error": t("Only the vendor assigns leads.")}
    try:
        lead = create_assigned_lead(user, body)
        return 201, {"lead": lead}
    except LeadFieldsChanged as exc:
        return 409, {"error": str(exc), "code": "stale_lead_fields"}
    except (PermissionError, ValueError, StaleRevisionError, OperationConflictError,
            LeadWorkflowConflict) as exc:
        return _workflow_error_response(exc)


def handle_precheck(handler, user: dict, body: dict):
    if user["role"] != "partner":
        return 403, {"error": t("Only a partner can check for duplicates.")}
    try:
        result = precheck_duplicate(user, body)
        return 200, result
    except ValueError as e:
        return 400, {"error": str(e)}


def handle_update(handler, user: dict, lead_id: str, body: dict):
    """
    PATCH /api/leads/{id} -- the general-purpose lead update.

    Two things happen before anything is written. A registration decision is
    handed to the canonical operation rather than performed here, so there is
    one set of rules instead of two; and the versioning metadata is required,
    because a write that does not say which version it read is last-write-wins
    by another name.
    """
    if not isinstance(body, dict):
        return 400, {"error": t("Invalid update data.")}

    body = dict(body)
    expected_revision = body.pop("expected_revision", None)
    operation_id = body.pop("operation_id", None)

    try:
        expected_revision, operation_id = _workflow_metadata(
            expected_revision, operation_id
        )
    except ValueError as exc:
        return 400, {"error": str(exc), "code": "missing_revision"}

    if "registration_status" in body:
        # Deciding and moving the sale are different questions. Answering both
        # in one request would make the outcome depend on statement order, so
        # the request is refused whole rather than half-applied.
        conflicting = [f for f in ("status", "commission", "commission_override_pct")
                       if f in body]
        if conflicting:
            return 400, {
                "error": t("A decision on the submission cannot be combined with a stage or"
                           " commission change. Send them separately."),
                "code": "mixed_decision",
            }

        action = _REGISTRATION_STATUS_TO_ACTION.get(body.get("registration_status"))
        if action is None:
            return 400, {
                "error": t("A submission cannot be moved back to pending."),
                "code": "undecide_not_allowed",
            }
        try:
            lead = apply_registration_action(
                lead_id, user, action=action,
                message=body.get("registration_message"),
                expected_revision=expected_revision, operation_id=operation_id,
            )
            return 200, {"lead": lead}
        except (PermissionError, ValueError, StaleRevisionError, OperationConflictError,
                LeadWorkflowConflict) as exc:
            return _workflow_error_response(exc)

    try:
        lead = update_lead(lead_id, user, body,
                           expected_revision=expected_revision,
                           operation_id=operation_id)
        return 200, {"lead": lead}
    except (PermissionError, ValueError, StaleRevisionError, OperationConflictError,
            LeadWorkflowConflict) as exc:
        return _workflow_error_response(exc)



def _workflow_error_response(exc):
    """
    One mapping, used by both workflow endpoints.

    LeadNotFoundError is deliberately checked before ValueError: it subclasses
    ValueError so that every older caller catching ValueError keeps working,
    which means order is what separates "no such lead" (404) from "bad input"
    (400).
    """
    if isinstance(exc, PermissionError):
        return 403, {"error": str(exc)}
    if isinstance(exc, LeadNotFoundError):
        return 404, {"error": str(exc), "code": "not_found"}
    if isinstance(exc, StaleRevisionError):
        return 409, {
            "error": str(exc),
            "code": "stale_revision",
            "current_revision": exc.actual_revision,
        }
    if isinstance(exc, LeadWorkflowConflict):
        return 409, {"error": str(exc), "code": exc.code}
    if isinstance(exc, OperationConflictError):
        return 409, {"error": str(exc), "code": exc.code}
    if isinstance(exc, ValueError):
        return 400, {"error": str(exc)}
    raise exc


def handle_registration(handler, user: dict, lead_id: str, body: dict):
    """POST /api/leads/{id}/registration -- the producer's decision."""
    try:
        body = parse_allowed_fields(
            body, ("action", "message", "expected_revision", "operation_id"),
            N_("Decision data"),
        )
        lead = apply_registration_action(
            lead_id, user,
            action=body.get("action"),
            message=body.get("message"),
            expected_revision=body.get("expected_revision"),
            operation_id=body.get("operation_id"),
        )
        return 200, {"lead": lead}
    except (PermissionError, ValueError, StaleRevisionError, OperationConflictError,
            LeadWorkflowConflict) as exc:
        return _workflow_error_response(exc)


def handle_information(handler, user: dict, lead_id: str, body: dict):
    """POST /api/leads/{id}/information -- the partner's answer."""
    try:
        body = parse_allowed_fields(
            body, ("message", "expected_revision", "operation_id"),
            N_("Reply data"),
        )
        lead = respond_to_information(
            lead_id, user,
            message=body.get("message"),
            expected_revision=body.get("expected_revision"),
            operation_id=body.get("operation_id"),
        )
        return 200, {"lead": lead}
    except (PermissionError, ValueError, StaleRevisionError, OperationConflictError,
            LeadWorkflowConflict) as exc:
        return _workflow_error_response(exc)


def handle_bulk(handler, user: dict, body: dict):
    """Validate the whole envelope, then decide each lead independently."""
    if not is_staff(user):
        return 403, {"error": t("Only an administrator can run bulk actions.")}

    try:
        body = parse_allowed_fields(body, ("action", "items"), N_("Bulk action data"))
        action = parse_enum(body.get("action"), "action", ("accept", "reject"))
        items = body.get("items")
        if not isinstance(items, list) or not items or len(items) > 1000:
            raise ValueError(t("Choose from 1 to 1000 leads."))
        normalized = []
        seen_ids, seen_operations = set(), set()
        for item in items:
            item = parse_allowed_fields(item,
                ("id", "expected_revision", "operation_id", "message"), N_("Lead data"))
            lead_id = _parse_lead_id(item.get("id"))
            revision, operation_id = _workflow_metadata(
                item.get("expected_revision"), item.get("operation_id"))
            message = _workflow_message(item.get("message"), N_("Rejection reason"), action == "reject")
            if lead_id in seen_ids or operation_id in seen_operations:
                raise ValueError(t("A lead and an operation id cannot repeat within one batch."))
            seen_ids.add(lead_id)
            seen_operations.add(operation_id)
            normalized.append((lead_id, revision, operation_id, message))
    except ValueError as exc:
        return 400, {"error": str(exc)}

    results = {"ok": [], "error": []}
    for lead_id, revision, operation_id, message in normalized:
        try:
            lead = apply_registration_action(lead_id, user, action, message, revision, operation_id)
            results["ok"].append({"id": lead_id, "status": 200, "lead": lead})
        except (ValueError, PermissionError, StaleRevisionError,
                OperationConflictError, LeadWorkflowConflict) as exc:
            status, error = _workflow_error_response(exc)
            results["error"].append({"id": lead_id, "status": status,
                "reason": error["error"],
                "code": error.get("code", "forbidden" if status == 403 else "invalid_request")})
        except Exception as exc:
            results["error"].append({**_unexpected_bulk_item(lead_id, exc), "status": 500})
    # Canonical decisions own audit and notifications; a batch-level audit
    # here would repeat on every retry, including a completely replayed batch.
    return 200, results
