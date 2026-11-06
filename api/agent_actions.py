"""Server-side confirmation workflow for AI-proposed state changes.

The model can only prepare an action. A separate authenticated request claims
the short-lived token, revalidates current domain state and performs the write.
"""

import hashlib
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta

from api import (
    audit, bonus_defs, commission_tiers, lead_fields, lead_stages, leads,
    products, registration_fields, users,
)
from api.authz import can_configure
from api.db import OperationConflictError, StaleRevisionError, get_conn
from api.i18n import t


logger = logging.getLogger(__name__)

ACTION_TTL_SECONDS = 10 * 60


class ActionError(ValueError):
    def __init__(self, message, status=400, code="invalid_action"):
        super().__init__(message)
        self.status = status
        self.code = code



def _stored_key(token):
    """What the database keeps for a confirmation token: its SHA-256.

    Like every other bearer secret here, the token itself exists only in the
    response that carries it, so a database dump holds no usable one.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def _utcnow():
    return datetime.utcnow()


def _cleanup(conn, now_text):
    conn.execute("DELETE FROM agent_pending_actions WHERE expires_at <= ?", (now_text,))


def create_pending_action(user, action_type, payload, card):
    """Persist minimal confirmation state and return a safe UI card payload."""
    now = _utcnow()
    token = secrets.token_urlsafe(24)
    conn = get_conn()
    try:
        _cleanup(conn, now.isoformat())
        # Only the newest proposal for a user remains actionable. This keeps
        # sensitive transient payloads short-lived and avoids ambiguous cards.
        conn.execute(
            "DELETE FROM agent_pending_actions WHERE user_id=? AND status='pending'",
            (user["id"],),
        )
        conn.execute(
            """INSERT INTO agent_pending_actions
               (token, user_id, action_type, payload, status, created_at, expires_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                _stored_key(token),
                user["id"],
                action_type,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                "pending",
                now.isoformat(),
                (now + timedelta(seconds=ACTION_TTL_SECONDS)).isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    result = dict(card)
    result["id"] = token
    result["expires_in"] = ACTION_TTL_SECONDS
    return result


def _claim_action(user, token):
    if not isinstance(token, str) or not 24 <= len(token) <= 128:
        raise ActionError(t("Invalid confirmation."), 404, "not_found")

    now_text = _utcnow().isoformat()
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT token, user_id, action_type, payload, status, expires_at
               FROM agent_pending_actions WHERE token=? AND user_id=?""",
            (_stored_key(token), user["id"]),
        ).fetchone()
        if row is None:
            raise ActionError(t("This proposal does not exist or has already been used."), 404, "not_found")
        if row["expires_at"] <= now_text:
            conn.execute("DELETE FROM agent_pending_actions WHERE token=?", (_stored_key(token),))
            conn.commit()
            raise ActionError(t("This proposal has expired. Ask the assistant for a new one."), 409, "expired")
        changed = conn.execute(
            """UPDATE agent_pending_actions SET status='executing'
               WHERE token=? AND user_id=? AND status='pending'""",
            (_stored_key(token), user["id"]),
        ).rowcount
        conn.commit()
        if changed != 1:
            raise ActionError(t("This proposal is already running or has been used."), 409, "replayed")
        return row["action_type"], json.loads(row["payload"])
    finally:
        conn.close()


def _delete_action(token):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM agent_pending_actions WHERE token=?", (_stored_key(token),))
        conn.commit()
    finally:
        conn.close()


def _same_submission_preview(expected, current):
    keys = ("policy", "queue_position", "queue_share")
    return all(expected.get(key) == current.get(key) for key in keys)


def _same_update_preview(payload, current):
    if payload.get("snapshot") != current.get("snapshot"):
        return False
    expected = payload.get("preview", {})
    fresh = current.get("preview", {})
    keys = ("old_status", "new_status", "commission", "total_percent", "queue_share")
    return all(expected.get(key) == fresh.get(key) for key in keys)


# ─── translate_labels: PL/EN names of stages, fields, bonuses, tiers and
#     products the producer named himself ─────────────────────

# One canonical writer per kind, called with exactly one *_en field -- the
# same functions Settings itself calls, so the permission check, the
# length limit and the audit entry are the ones that already exist. The
# assistant gets no private door into the configuration.
_TRANSLATION_WRITERS = {
    "stage": lambda item, user: lead_stages.update_stage(
        item["id"], {"name_en": item["english"]}, user
    ),
    "lead_field": lambda item, user: lead_fields.update_field(
        item["id"], {"label_en": item["english"]}, user
    ),
    "registration_field": lambda item, user: registration_fields.update_field(
        item["id"], {"label_en": item["english"]}, user
    ),
    "bonus_name": lambda item, user: bonus_defs.update_bonus(
        item["id"], {"name_en": item["english"]}, user
    ),
    "bonus_description": lambda item, user: bonus_defs.update_bonus(
        item["id"], {"description_en": item["english"]}, user
    ),
    "tier": lambda item, user: commission_tiers.update_tier(
        item["id"], {"name_en": item["english"]}, user
    ),
    "product": lambda item, user: products.update_product(
        item["id"], {"name_en": item["english"]}, user
    ),
}

# A per-kind readback of the SAME column a writer above touches. Every
# canonical writer already ends with `return get_by_id(...)` after its own
# commit (api/bonus_defs.py, api/commission_tiers.py, api/products.py); this
# mirrors that same post-commit read so an exception raised AFTER a real
# commit (e.g. inside the writer's own get_by_id) can be told apart from one
# that never wrote anything.
_TRANSLATION_READERS = {
    "stage": lambda item_id: (lead_stages.get_by_id(item_id) or {}).get("name_en"),
    "lead_field": lambda item_id: next(
        (f.get("label_en") for f in lead_fields.list_fields() if f["id"] == item_id),
        None,
    ),
    "registration_field": lambda item_id: (
        registration_fields.get_by_id(item_id) or {}
    ).get("label_en"),
    "bonus_name": lambda item_id: (bonus_defs.get_by_id(item_id) or {}).get("name_en"),
    "bonus_description": lambda item_id: (
        bonus_defs.get_by_id(item_id) or {}
    ).get("description_en"),
    "tier": lambda item_id: (commission_tiers.get_by_id(item_id) or {}).get("name_en"),
    "product": lambda item_id: (products.get_by_id(item_id) or {}).get("name_en"),
}


def _public_translation_item(item, **extra):
    return {
        "kind": item["kind"],
        "id": item["id"],
        "polish": item["polish"],
        "english": item["english"],
        **extra,
    }


def _reauthorize_translate_labels(user, lang):
    """Re-check the CURRENT role, read fresh from the database, right before
    the first writer runs -- never the `user` dict the click arrived with.

    A `user` dict can be stale (the session predates a demotion, or
    was captured earlier in a long-lived request). Two checks, both current:
    `can_configure` (the general Settings gate, which also admits
    "manager") AND this tool's OWN admin-only entry in TOOL_REGISTRY --
    deliberately narrower than can_configure, so a demoted admin->manager
    must still be refused HERE even though he could still edit Settings by
    hand as a manager. Losing the admin role to manager or partner returns
    403 before any writer is called, for the whole batch.
    """
    from api.agent_tools import TOOL_REGISTRY  # deferred: agent_tools imports us

    current = users.get_user(user["id"])
    if current is None or not current.get("active"):
        raise ActionError(
            t("The account is inactive.", lang=lang),
            403, "forbidden",
        )
    role = current["role"]
    allowed_roles = TOOL_REGISTRY["prepare_translate_labels"]["roles"]
    if not can_configure({"role": role}) or role not in allowed_roles:
        raise ActionError(
            t("No permission to save the translations.", lang=lang),
            403, "forbidden",
        )
    # A fresh dict carrying the CURRENT role: every writer below re-checks
    # can_configure() itself, and
    # it must see this role, not whatever the click's `user` still says.
    return {**user, "role": role}


def _confirm_translate_labels(user, payload, lang):
    """Execute one item at a time; stop honestly at the first problem.

    Canonical writers commit independently -- there is no shared
    transaction across stage/bonus/tier/product tables, so this never
    promises atomicity. On ANY error the batch stops: everything already
    written stays written, the failing (or ambiguous) item is reported by
    name and reason, and everything after it is `not_attempted`. The whole
    batch is never reported as a success when only part of it saved.
    """
    acting_user = _reauthorize_translate_labels(user, lang)

    items = payload["translations"]
    written = []
    not_attempted = []
    failed = None
    outcome_unknown = None

    for index, item in enumerate(items):
        writer = _TRANSLATION_WRITERS.get(item["kind"])
        if writer is None:
            failed = _public_translation_item(
                item, code="unknown_action",
                message=t("This operation is not supported.", lang=lang),
            )
            not_attempted = items[index + 1:]
            break
        try:
            writer(item, acting_user)
        except PermissionError as denied:
            failed = _public_translation_item(
                item, code="forbidden", message=str(denied)
            )
            not_attempted = items[index + 1:]
            break
        except ValueError as invalid:
            # A name (or the row itself) that vanished, or became invalid,
            # between the proposal and the click -- raised by the writer's
            # OWN validation, always BEFORE it touches the database.
            failed = _public_translation_item(
                item, code="stale_action", message=str(invalid)
            )
            not_attempted = items[index + 1:]
            break
        except Exception as unexpected:  # noqa: BLE001 -- classified below, never re-raised raw
            request_id = uuid.uuid4().hex
            logger.error(
                "agent_translate_labels_unexpected_error request_id=%s "
                "exception=%s kind=%s id=%s",
                request_id, type(unexpected).__name__, item["kind"], item["id"],
            )
            reader = _TRANSLATION_READERS.get(item["kind"])
            try:
                current_value = reader(item["id"]) if reader else None
                confirmed_by_read = reader is not None and current_value == item["english"]
            except Exception:  # noqa: BLE001 -- the read itself is unreliable now
                confirmed_by_read = False
            if confirmed_by_read:
                # The writer's OWN commit already happened (every canonical
                # writer here ends with its own post-commit get_by_id) -- the
                # exception came from somewhere after that. Reading the value
                # back and finding it correct is what "confirmed by read"
                # means: this is not a failure to report, and not proof of
                # who changed it in a race -- just what the row says now.
                written.append(_public_translation_item(
                    item,
                    warning=t("The write succeeded, but the operation reported an error afterwards.", lang=lang),
                ))
            else:
                outcome_unknown = _public_translation_item(
                    item, code="outcome_unknown",
                    message=t("Could not confirm the outcome (request {request_id}).",
                              lang=lang, request_id=request_id),
                )
            not_attempted = items[index + 1:]
            break
        else:
            written.append(_public_translation_item(item))

    # A "warning" on an otherwise-written item means the write happened but
    # something about the operation still needs the producer's attention
    # (confirmed only by reading it back, not by the writer's own clean
    # return) -- that is not a silent full success either.
    had_warning = any("warning" in row for row in written)
    partial = bool(failed or outcome_unknown or not_attempted or had_warning)
    if failed or outcome_unknown or not_attempted:
        response = t("Partially saved: {written} of {total}. Ask for a new proposal for the rest.",
                     lang=lang, written=len(written), total=len(items))
    elif had_warning:
        response = t("All translations saved ({written}), but one operation reported an "
                     "error afterwards — check the result.", lang=lang, written=len(written))
    elif written:
        response = t("English names saved: {written}.", lang=lang, written=len(written))
    else:
        response = t("There was nothing to save.", lang=lang)

    public_result = {
        "translations": {
            "partial": partial,
            "written": written,
            "failed": failed,
            "outcome_unknown": outcome_unknown,
            "not_attempted": [_public_translation_item(x) for x in not_attempted],
        }
    }
    return response, public_result


def _confirm_registration_decision(user, token, payload, lang):
    """Execute a confirmed registration decision through the canonical
    domain operation, the same one the producer's own buttons call."""
    decision = payload["registration"]
    current = leads.preview_registration_action(
        payload["lead_id"], user, decision["action"], decision["message"]
    )
    if current["snapshot"] != payload.get("snapshot"):
        raise ActionError(
            t("The lead changed since the proposal. Ask for a new proposal.", lang=lang),
            409,
            "stale_action",
        )
    # The confirmation token is the replay key, and the revision the card
    # was built from is the compare-and-swap guard: a lead changed between
    # the preview above and the write below is refused, not overwritten.
    try:
        result = leads.apply_registration_action(
            payload["lead_id"], user, decision["action"],
            message=decision["message"],
            expected_revision=payload["snapshot"]["revision"],
            operation_id=f"agent-{_stored_key(token)}",
        )
    except (StaleRevisionError, OperationConflictError):
        raise ActionError(
            t("The lead changed since the proposal. Ask for a new proposal.", lang=lang),
            409,
            "stale_action",
        )
    moved = t(leads.REGISTRATION_LABELS.get(
        result["registration_status"], result["registration_status"]
    ), lang=lang)
    public_result = {
        "id": result["id"],
        "status": result["status"],
        "registration_status": result["registration_status"],
        "commission": result.get("commission"),
        "commission_projected": result.get("commission_projected"),
    }
    return f"Lead {result['id']}: {moved}.", public_result


def confirm_action(user, token):
    """Claim, revalidate and execute one action exactly once."""
    action_type, payload = _claim_action(user, token)
    # The card said what it would do, in one language. The sentence reporting
    # what it DID has to be in the same one.
    lang = payload.get("lang", "en")
    try:
        if action_type == "create_lead":
            current = leads.preview_lead_submission(user, payload["data"])
            if not _same_submission_preview(payload["preview"], current["preview"]):
                raise ActionError(
                    t("The queue or the submission rules changed. Ask for a new proposal.", lang=lang),
                    409,
                    "stale_action",
                )
            result = leads.submit_lead(user, current["data"])
            response = t("Lead {lead} was submitted safely.", lang=lang, lead=result['id'])
            public_result = {
                "id": result["id"],
                "client_name": result["client_name"],
                "company": result["company"],
                "status": result["status"],
                "queue_position": result["queue_position"],
            }
        elif action_type == "update_lead_status" and "registration" in payload:
            response, public_result = _confirm_registration_decision(
                user, token, payload, lang
            )
        elif action_type == "update_lead_status":
            current = leads.preview_lead_update(
                payload["lead_id"], user, payload["data"]
            )
            if not _same_update_preview(payload, current):
                raise ActionError(
                    t("The lead or the commission figure changed. Ask for a new proposal.", lang=lang),
                    409,
                    "stale_action",
                )
            # The confirmation token IS the replay key: it is already
            # single-use and unique per proposal, so a click that gets lost on
            # the wire and is retried lands as the same operation rather than
            # a second write.
            result = leads.update_lead(
                payload["lead_id"], user, current["data"],
                operation_id=f"agent-{_stored_key(token)}",
            )
            commission = result.get("commission")
            # Report whichever axis the update actually moved: a registration
            # decision or a step through the sale. Naming the stage for a
            # lead that has no stage yet would read as nonsense.
            moved = (
                result["status"]
                if "status" in current["data"]
                else t(leads.REGISTRATION_LABELS.get(
                    result["registration_status"], result["registration_status"]
                ), lang=lang)
            )
            response = f"Lead {result['id']}: {moved}."
            if commission is not None:
                response += t(" Commission: {amount} PLN.", lang=lang,
                              amount=f"{commission:,.2f}")
            public_result = {
                "id": result["id"],
                "status": result["status"],
                "registration_status": result["registration_status"],
                "commission": commission,
                "commission_projected": result.get("commission_projected"),
            }
        elif action_type == "translate_labels":
            response, public_result = _confirm_translate_labels(user, payload, lang)
        else:
            raise ActionError(t("This operation is not supported."), 400, "unknown_action")
    finally:
        # No retry after the execution boundary: it prevents a partial external
        # side effect from being repeated if a downstream notification fails.
        _delete_action(token)

    audit.log(user, "agent.action_confirm", "agent", action_type, "confirmed in the app")
    return {"response": response, "result": public_result}


def cancel_action(user, token):
    if not isinstance(token, str) or not 24 <= len(token) <= 128:
        raise ActionError(t("Invalid confirmation."), 404, "not_found")
    conn = get_conn()
    try:
        # Read the language the card was written in before the row goes: the
        # sentence that replaces the card has to match it.
        row = conn.execute(
            """SELECT payload FROM agent_pending_actions
               WHERE token=? AND user_id=? AND status='pending'""",
            (_stored_key(token), user["id"]),
        ).fetchone()
        lang = "en"
        if row:
            try:
                lang = json.loads(row["payload"]).get("lang", "en")
            except (ValueError, TypeError, AttributeError):
                lang = "en"
        deleted = conn.execute(
            """DELETE FROM agent_pending_actions
               WHERE token=? AND user_id=? AND status='pending'""",
            (_stored_key(token), user["id"]),
        ).rowcount
        conn.commit()
    finally:
        conn.close()
    if deleted != 1:
        raise ActionError(t("This proposal does not exist or has already been used."), 404, "not_found")
    audit.log(user, "agent.action_cancel", "agent", "", "cancelled in the app")
    return {"response": t("The proposal was cancelled.", lang=lang)}


def handle_confirm(handler, user, token):
    try:
        return 200, confirm_action(user, token)
    except ActionError as exc:
        return exc.status, {"error": str(exc), "code": exc.code}
    except PermissionError as exc:
        return 403, {"error": str(exc), "code": "forbidden"}
    except ValueError as exc:
        return 400, {"error": str(exc), "code": "validation_error"}
    except leads.LeadWorkflowConflict as exc:
        # Defense in depth: preview_lead_update already blocks a terminal-
        # exit proposal before a confirmation card is even created, so this
        # is not expected to fire in practice -- but if update_lead itself
        # ever refuses at execution time, this is still a clean 409, not an
        # unhandled 500.
        return 409, {"error": str(exc), "code": exc.code}


def handle_cancel(handler, user, token):
    try:
        return 200, cancel_action(user, token)
    except ActionError as exc:
        return exc.status, {"error": str(exc), "code": exc.code}
