"""Affiliate programme: referral links and shop orders that earn commission.

A partner shares a personal link (/r/<code>). The producer's shop remembers the
code after the visitor's consent and, when that visitor pays, sends a signed
webhook here. The order becomes a conversion: PENDING until a person (or the
administrator's auto-approval after the refund window) approves it, and then a
won lead of the partner, so the existing commission rules, payouts and
statistics apply to it unchanged (api/leads.py, create_affiliate_lead).

The webhook is its own door, not the read-only /api/v1 gateway, and it has no
session or API key: the shop proves itself with an HMAC-SHA256 signature made
with the instance's affiliate secret.

    PartnerType-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256(secret, "<t>." + body)>

Nothing personal is kept: no buyer e-mail, name or IP address and no request
body, only the order id, net amount, currency, time, partner code or click id
and the SHA-256 of the body. See docs/AFFILIATE.md.
"""
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import re
import secrets
import string
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from cryptography.fernet import Fernet, InvalidToken

from api import audit
from api.config import secret as config_secret
from api.db import get_conn
from api.i18n import N_, t
from api.postgres import IntegrityError, system_context

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "PartnerType-Signature"
SIGNATURE_TOLERANCE_SECONDS = 300
MAX_BODY_BYTES = 64 * 1024
WEBHOOK_PER_MINUTE = 120
CLICKS_PER_MINUTE_PER_VISITOR = 30
CLICKS_PER_MINUTE = 1200
# Clicks are kept for counting and for click-id attribution, then swept by
# api.db.run_maintenance.
CLICK_RETENTION_DAYS = 400
MAX_SHOP_URL = 500
SNIPPET_FILE = Path(__file__).resolve().parent.parent / "js" / "pt-ref.js"

SECRET_PREFIX = "ptw_"
FINGERPRINT_CHARS = 8
CODE_LENGTH = 10
_CODE_ALPHABET = string.ascii_lowercase + string.digits
MAX_ORDER_ID = 128
# The oldest order a shop may still report; anything older is a mistake.
MAX_ORDER_AGE_DAYS = 365
# A refund that arrives before its order waits for it as long as that order
# may still be reported: a refund comes after its order was placed, so this
# covers any order the shop may yet send. Forgetting it sooner would let a
# late order (a re-sent backlog) look payable and be approved automatically.
EARLY_REFUND_RETENTION_DAYS = MAX_ORDER_AGE_DAYS

KEY_ENABLED = "affiliate_enabled"
KEY_SHOP_URL = "affiliate_shop_url"
KEY_COOKIE_DAYS = "affiliate_cookie_days"
KEY_AUTO_APPROVE = "affiliate_auto_approve"
KEY_AUTO_APPROVE_DAYS = "affiliate_auto_approve_days"

DEFAULTS = {
    KEY_ENABLED: "0",
    KEY_SHOP_URL: "",
    KEY_COOKIE_DAYS: "30",
    KEY_AUTO_APPROVE: "0",
    KEY_AUTO_APPROVE_DAYS: "14",
}

CONVERSION_STATUSES = (
    "pending", "unattributed", "approved", "rejected", "refunded",
    "refund_after_approval", "reversed",
)

STRIPE_SIGNATURE_HEADER = "Stripe-Signature"
STRIPE_PAID_EVENTS = ("checkout.session.completed", "checkout.session.async_payment_succeeded")
STRIPE_REFUND_EVENT = "charge.refunded"
# Currencies Stripe counts in whole units rather than hundredths.
STRIPE_ZERO_DECIMAL = frozenset(
    "BIF CLP DJF GNF JPY KMF KRW MGA PYG RWF UGX VND VUV XAF XOF XPF".split())
_STRIPE_SECRET_RE = re.compile(r"^whsec_[A-Za-z0-9+/=_-]{8,200}$")

ORDER_PAID = "order.paid"
ORDER_REFUNDED = "order.refunded"
PING = "ping"
EVENTS = (ORDER_PAID, ORDER_REFUNDED, PING)
_EVENT_FIELDS = {"event", "order_id", "amount", "currency", "ref", "occurred_at"}

_ORDER_ID_RE = re.compile(r"^[\x21-\x7e]{1,128}$")
_REF_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_AMOUNT_RE = re.compile(r"^\d{1,12}(\.\d{1,2})?$")


def money(value):
    """An amount as an exact two-place Decimal, from text, int or float.

    The database layer hands NUMERIC back as int or float; going through its
    text form keeps 99.99 from turning into 99.98999...
    """
    return Decimal(str(value)).quantize(Decimal("0.01"))


class AffiliateError(Exception):
    """A refusal with the HTTP status and a stable code for the caller."""

    def __init__(self, status, code, message, headers=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers or {}


# ─── SETTINGS ────────────────────────────────────────────────────────────────

def _read_settings(conn):
    rows = conn.execute(
        "SELECT key, value FROM settings WHERE key = ANY(?)",
        (list(DEFAULTS) + ["currency"],),
    ).fetchall()
    stored = {row["key"]: row["value"] for row in rows}
    values = {key: stored.get(key, default) for key, default in DEFAULTS.items()}
    values["currency"] = (stored.get("currency") or "PLN").strip().upper()
    return values


def current_settings():
    """The affiliate settings with their defaults, and the instance currency."""
    conn = get_conn()
    try:
        return _read_settings(conn)
    finally:
        conn.close()


def is_enabled():
    return current_settings()[KEY_ENABLED] == "1"


def _parse_switch(value, field):
    if not isinstance(value, bool):
        raise ValueError(t("Field '{field}' must be true or false.", field=field))
    return "1" if value else "0"


def _parse_days(value, field):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 365:
        raise ValueError(t("Field '{field}' must be a whole number of days from 1 to 365.",
                           field=field))
    return str(value)


def parse_shop_url(value):
    """The one address /r/ may send a visitor to.

    https only (plain http just for a shop on this machine), no credentials and
    no fragment, so the redirect cannot be bent into anything else.
    """
    if not isinstance(value, str):
        raise ValueError(t("Enter the shop address."))
    value = value.strip()
    if value == "":
        return ""
    invalid = ValueError(t("Enter the full shop address, starting with https://."))
    if len(value) > MAX_SHOP_URL or any(ord(char) < 33 for char in value):
        raise invalid
    try:
        parts = urlsplit(value)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        raise invalid
    local = host in ("localhost", "127.0.0.1")
    if (parts.scheme not in ("https", "http") or (parts.scheme == "http" and not local)
            or not host or parts.username is not None or parts.password is not None
            or parts.fragment or port == 0):
        raise invalid
    return value


_SETTING_FIELDS = {
    "enabled": (KEY_ENABLED, _parse_switch),
    "shop_url": (KEY_SHOP_URL, lambda value, field: parse_shop_url(value)),
    "cookie_days": (KEY_COOKIE_DAYS, _parse_days),
    "auto_approve": (KEY_AUTO_APPROVE, _parse_switch),
    "auto_approve_days": (KEY_AUTO_APPROVE_DAYS, _parse_days),
}


def _require_settings_admin(user):
    from api.authz import can_manage_integrations
    if not can_manage_integrations(user):
        raise PermissionError(t("Only the administrator configures the affiliate programme."))


def snippet(cookie_days):
    """The code the administrator pastes into the shop."""
    source = SNIPPET_FILE.read_text(encoding="utf-8")
    return (f"<script>window.PartnerTypeRefDays = {int(cookie_days)};</script>\n"
            f"<script>\n{source}</script>\n")


def settings_view(user):
    """Everything the Affiliate settings screen shows; never a secret."""
    from api.config import Config
    _require_settings_admin(user)
    conn = get_conn()
    try:
        values = _read_settings(conn)
        secrets_ = secret_state(conn)
    finally:
        conn.close()
    base = Config.load().base_url

    def described(kind):
        state = secrets_.get(kind)
        return None if state is None else {
            "fingerprint": state["fingerprint"],
            "created_at": _iso(state["created_at"])}

    return {
        "enabled": values[KEY_ENABLED] == "1",
        "shop_url": values[KEY_SHOP_URL],
        "cookie_days": int(values[KEY_COOKIE_DAYS]),
        "auto_approve": values[KEY_AUTO_APPROVE] == "1",
        "auto_approve_days": int(values[KEY_AUTO_APPROVE_DAYS]),
        "currency": values["currency"],
        "webhook_url": f"{base}/api/affiliate/webhook",
        "stripe_webhook_url": f"{base}/api/affiliate/stripe",
        "stripe_secret": described("stripe"),
        "secret": described("webhook"),
        "snippet": snippet(values[KEY_COOKIE_DAYS]),
    }


def update_settings(user, data):
    """Change any of the affiliate settings; each change is audited."""
    _require_settings_admin(user)
    if not isinstance(data, dict) or not data:
        raise ValueError(t("No data to update."))
    unknown = sorted(set(data) - set(_SETTING_FIELDS))
    if unknown:
        raise ValueError(t("Unknown field: {field}.", field=unknown[0][:40]))
    updates = {}
    for name, value in data.items():
        key, parse = _SETTING_FIELDS[name]
        updates[key] = parse(value, name)
    with get_conn() as conn:
        for key, value in updates.items():
            conn.execute(
                """INSERT INTO settings (key, value) VALUES (?, ?)
                   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
                (key, value))
    for key, value in updates.items():
        audit.log(user, "affiliate.settings", "settings", key, value)
    return settings_view(user)


def send_test_event(user):
    """Sign a ping with the stored secret and run it through the webhook
    exactly as a shop's request would go; report what the shop would see."""
    _require_settings_admin(user)
    conn = get_conn()
    try:
        secret = _load_secret(conn, "webhook")
    finally:
        conn.close()
    if secret is None:
        raise AffiliateError(409, "no_secret", t("Create the signing secret first."))
    body = json.dumps({"event": PING}).encode()
    try:
        status, payload = receive_webhook({SIGNATURE_HEADER: sign(secret, body)}, body,
                                          test_event=True)
    except AffiliateError as exc:
        return {"ok": False, "status": exc.status, "code": exc.code, "error": exc.message}
    return {"ok": status == 200, "status": status, "response": payload}


# ─── SECRETS ─────────────────────────────────────────────────────────────────

def _cipher():
    """A key derived from the instance session secret, never stored.

    A signature can only be checked with the secret itself, so unlike an API
    key it is kept encrypted rather than hashed (the same scheme as second
    factor secrets, with its own derivation). Rotating the session secret makes
    it unreadable; the administrator then creates a new one.
    """
    material = config_secret("PARTNERTYPE_SESSION_SECRET", True).encode()
    digest = hashlib.sha256(b"partnertype-affiliate:" + material).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _fingerprint(raw):
    return raw[:len(SECRET_PREFIX) + FINGERPRINT_CHARS]


def _store_secret(conn, kind, raw, user_id):
    conn.execute(
        """INSERT INTO affiliate_secrets (kind, ciphertext, fingerprint, created_by)
           VALUES (?, ?, ?, ?)
           ON CONFLICT (kind) DO UPDATE
              SET ciphertext = EXCLUDED.ciphertext,
                  fingerprint = EXCLUDED.fingerprint,
                  created_at = now(),
                  created_by = EXCLUDED.created_by""",
        (kind, _cipher().encrypt(raw.encode()).decode(), _fingerprint(raw), user_id),
    )


def create_webhook_secret(user):
    """Create or rotate the shop's signing secret; the only time it is shown.

    The previous secret stops working at once.
    """
    _require_settings_admin(user)
    raw = SECRET_PREFIX + secrets.token_urlsafe(32)
    with get_conn() as conn:
        _store_secret(conn, "webhook", raw, user["id"])
    audit.log(user, "affiliate.secret", "settings", "affiliate", _fingerprint(raw))
    return {"secret": raw, "fingerprint": _fingerprint(raw)}


def set_stripe_secret(user, raw):
    """Store the signing secret of the Stripe endpoint, pasted from Stripe."""
    _require_settings_admin(user)
    raw = raw.strip() if isinstance(raw, str) else ""
    if not _STRIPE_SECRET_RE.fullmatch(raw):
        raise ValueError(t("Paste the endpoint's signing secret from Stripe; "
                           "it starts with whsec_."))
    with get_conn() as conn:
        _store_secret(conn, "stripe", raw, user["id"])
    audit.log(user, "affiliate.stripe_secret", "settings", "affiliate", _fingerprint(raw))
    return settings_view(user)


def secret_state(conn):
    """{kind: {fingerprint, created_at}} without the secrets themselves."""
    rows = conn.execute(
        "SELECT kind, fingerprint, created_at FROM affiliate_secrets").fetchall()
    return {row["kind"]: {"fingerprint": row["fingerprint"],
                          "created_at": row["created_at"]} for row in rows}


def _load_secret(conn, kind):
    row = conn.execute(
        "SELECT ciphertext FROM affiliate_secrets WHERE kind = ?", (kind,)).fetchone()
    if row is None:
        return None
    try:
        return _cipher().decrypt(row["ciphertext"].encode()).decode()
    except InvalidToken:
        logger.error("affiliate_secret_unreadable kind=%s", kind)
        return None


# ─── LINKS ───────────────────────────────────────────────────────────────────

def _new_code():
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(CODE_LENGTH))


def _live_link(conn, partner_id):
    return conn.execute(
        """SELECT id, code, created_at FROM affiliate_links
            WHERE partner_id = ? AND revoked_at IS NULL""",
        (partner_id,),
    ).fetchone()


def ensure_link(user):
    """The partner's live referral link, created on first use."""
    if (user or {}).get("role") != "partner":
        raise PermissionError(t("Only a partner has a referral link."))
    if not is_enabled():
        raise AffiliateError(404, "affiliate_disabled",
                             t("The affiliate programme is switched off."))
    conn = get_conn()
    try:
        conn.begin()
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(?, 0))",
                     (f"affiliate-link:{user['id']}",))
        link = _live_link(conn, user["id"])
        if link is None:
            # The partner's own context cannot see other partners' codes, so
            # only the unique constraint can tell a collision; draw again then.
            for _ in range(5):
                conn.execute("SAVEPOINT affiliate_link_code")
                try:
                    conn.execute(
                        "INSERT INTO affiliate_links (partner_id, code) VALUES (?, ?)",
                        (user["id"], _new_code()),
                    )
                    break
                except IntegrityError as exc:
                    if getattr(exc.diag, "constraint_name", None) != "affiliate_links_code_key":
                        raise
                    conn.execute("ROLLBACK TO SAVEPOINT affiliate_link_code")
            else:
                raise RuntimeError("no free referral code")
            link = _live_link(conn, user["id"])
        conn.commit()
        return dict(link)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─── SIGNATURE ───────────────────────────────────────────────────────────────

def sign(secret, body, timestamp=None):
    """The header value a shop sends for `body` (bytes); used by tests and
    the "send test event" button, and documented for shop developers."""
    timestamp = int(time.time()) if timestamp is None else int(timestamp)
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"t={timestamp},v1={mac.hexdigest()}"


def _parse_signature(header):
    timestamp, candidates = None, []
    for part in (header or "").split(","):
        name, _, value = part.strip().partition("=")
        if name == "t" and value.isdigit() and len(value) <= 12:
            timestamp = int(value)
        elif name == "v1" and re.fullmatch(r"[0-9a-f]{64}", value):
            candidates.append(value)
    return timestamp, candidates


def verify_signature(header, body, secret, now=None):
    """Raise AffiliateError(401) unless `header` signs `body` with `secret`
    within the allowed clock difference. Constant-time comparison."""
    timestamp, candidates = _parse_signature(header)
    if timestamp is None or not candidates or not secret:
        raise AffiliateError(401, "bad_signature", t("The signature is missing or invalid."))
    now = int(time.time()) if now is None else int(now)
    if abs(now - timestamp) > SIGNATURE_TOLERANCE_SECONDS:
        raise AffiliateError(401, "stale_signature",
                             t("The signature is too old or from the future."))
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + body,
                        hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in candidates):
        raise AffiliateError(401, "bad_signature", t("The signature is missing or invalid."))


# ─── EVENTS ──────────────────────────────────────────────────────────────────

def _bad(message, **params):
    return AffiliateError(400, "invalid_body", t(message, **params))


def _parse_time(value, now):
    if value is None:
        return now
    if not isinstance(value, str) or len(value) > 40:
        raise _bad(N_("Field '{field}' must be an ISO 8601 date and time."), field="occurred_at")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise _bad(N_("Field '{field}' must be an ISO 8601 date and time."), field="occurred_at")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if parsed > now + timedelta(seconds=SIGNATURE_TOLERANCE_SECONDS):
        raise _bad(N_("The order time is in the future."))
    if parsed < now - timedelta(days=MAX_ORDER_AGE_DAYS):
        raise _bad(N_("The order is older than a year."))
    return parsed


def _parse_amount(value, required):
    if value is None and not required:
        return None
    if not isinstance(value, str) or not _AMOUNT_RE.fullmatch(value):
        raise _bad(N_("Field '{field}' must be a decimal number written as text, "
                   "for example \"199.00\"."), field="amount")
    try:
        return Decimal(value).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise _bad(N_("Field '{field}' must be a decimal number written as text, "
                   "for example \"199.00\"."), field="amount")


def parse_event(body, currency, now=None):
    """Validate a decoded webhook body; return the clean event.

    Unknown fields are refused rather than ignored, so a shop that sends buyer
    data by mistake learns it at once instead of believing it is stored.
    """
    now = now or datetime.now(timezone.utc)
    if not isinstance(body, dict):
        raise _bad(N_("The body must be a JSON object."))
    unknown = sorted(set(body) - _EVENT_FIELDS)
    if unknown:
        raise _bad(N_("Unknown field: {field}."), field=unknown[0][:40])
    event = body.get("event")
    if event not in EVENTS:
        raise _bad(N_("Field 'event' must be one of: {values}."), values=", ".join(EVENTS))
    if event == PING:
        return {"event": PING}
    order_id = body.get("order_id")
    if not isinstance(order_id, str) or not _ORDER_ID_RE.fullmatch(order_id):
        raise _bad(N_("Field 'order_id' is required: up to {max} printable ASCII characters."),
                   max=MAX_ORDER_ID)
    amount = _parse_amount(body.get("amount"), required=event == ORDER_PAID)
    sent_currency = body.get("currency")
    if sent_currency is not None:
        if not isinstance(sent_currency, str) or not re.fullmatch(r"[A-Za-z]{3}", sent_currency):
            raise _bad(N_("Field 'currency' must be a three-letter code such as {currency}."),
                       currency=currency)
        if sent_currency.upper() != currency:
            raise AffiliateError(
                422, "unsupported_currency",
                t("This instance accepts orders in {currency} only.", currency=currency))
    ref = body.get("ref")
    if ref is not None and ref != "":
        if not isinstance(ref, str) or not _REF_RE.fullmatch(ref):
            raise _bad(N_("Field 'ref' must be a partner code or a click id."))
    else:
        ref = None
    return {
        "event": event,
        "order_id": order_id,
        "amount": amount,
        "currency": currency,
        "ref": ref,
        "occurred_at": _parse_time(body.get("occurred_at"), now),
    }


def _attribution(conn, ref):
    """(partner_id, link_id, click_id) for a code or click id, or Nones.

    Only a live link of an active partner counts; anything else leaves the
    order unattributed for the administrator to assign by hand.
    """
    if not ref:
        return None, None, None
    row = conn.execute(
        """SELECT l.id AS link_id, l.partner_id, NULL AS click_id
             FROM affiliate_links l
            WHERE l.code = ? AND l.revoked_at IS NULL
           UNION ALL
           SELECT c.link_id, c.partner_id, c.click_id
             FROM affiliate_clicks c
             JOIN affiliate_links l ON l.id = c.link_id AND l.revoked_at IS NULL
            WHERE c.click_id = ?
            LIMIT 1""",
        (ref.lower() if len(ref) == CODE_LENGTH else ref, ref),
    ).fetchone()
    if row is None:
        return None, None, None
    partner = conn.execute(
        "SELECT active, role FROM users WHERE id = ?", (row["partner_id"],)).fetchone()
    if partner is None or not partner["active"] or partner["role"] != "partner":
        return None, None, None
    return row["partner_id"], row["link_id"], row["click_id"]


def _conversion_result(row):
    return {"status": row["status"], "conversion_id": row["id"]}


def _lock(conn, name):
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(?, 0))", (name,))


def _payment_lock(conn, payment_ref):
    """Serialises a Stripe order and a refund of the same payment that may
    arrive in either order."""
    _lock(conn, f"affiliate-payment:{payment_ref}")


def _hold_refund(conn, source, ref, amount=None, charged=None, refunded=None):
    """Keep a refund whose order has not arrived yet, inside the caller's
    transaction and lock. A generic refund carries the total refunded so far
    (None: in full); a Stripe one the charge and its refunded part."""
    conn.execute(
        """INSERT INTO affiliate_early_refunds
                  (source, ref, amount, charged_minor, refunded_minor)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT (source, ref) DO UPDATE
              SET amount = CASE WHEN affiliate_early_refunds.amount IS NULL
                                  OR EXCLUDED.amount IS NULL THEN NULL
                                ELSE GREATEST(affiliate_early_refunds.amount,
                                              EXCLUDED.amount) END,
                  charged_minor = EXCLUDED.charged_minor,
                  refunded_minor = GREATEST(affiliate_early_refunds.refunded_minor,
                                            EXCLUDED.refunded_minor)""",
        (source, ref, amount, charged, refunded))


def _take_held_refund(conn, source, refs, order_amount):
    """(refunded amount, when it arrived) of a refund held for this order
    under any of `refs`, or (None, None); every held row is used up, so none
    is left waiting for an order that has already come."""
    if not refs:
        return None, None
    rows = conn.execute(
        "DELETE FROM affiliate_early_refunds WHERE source = ? AND ref = ANY(?) RETURNING *",
        (source, list(refs))).fetchall()
    if not rows:
        return None, None
    # The ids of one payment hold the same refund; the largest total is the
    # latest one Stripe reported.
    held = max(rows, key=lambda row: row["refunded_minor"] or 0)
    if source == "stripe":
        charged, refunded = held["charged_minor"], held["refunded_minor"]
        amount = (order_amount if not charged or refunded >= charged else
                  (order_amount * Decimal(refunded) / Decimal(charged)).quantize(Decimal("0.01")))
    else:
        amount = (order_amount if held["amount"] is None
                  else min(money(held["amount"]), order_amount))
    return amount, held["received_at"]


def sweep_early_refunds(conn):
    """Forget held refunds whose order never came; called by the daily
    maintenance."""
    return conn.execute(
        "DELETE FROM affiliate_early_refunds "
        "WHERE received_at < now() - make_interval(days => ?)",
        (EARLY_REFUND_RETENTION_DAYS,)).rowcount


def record_order(event, body_sha256, source="webhook", payment_ref=None, other_refs=()):
    """Store one paid order once; return (status code, response body).

    `payment_ref` is the Stripe id a later refund is matched by; `other_refs`
    are further ids of the same payment, under which a refund that came first
    may also be held.

    The same order id with the same amount and currency is a retry and
    answers with the conversion it already created; with different data it is
    refused (409) and nothing changes. A refund that arrived first is applied
    at once, so the order starts out refunded (or partly) and never waits as
    if it were payable.
    """
    conn = get_conn()
    try:
        conn.begin()
        _lock(conn, f"affiliate-order:{event['order_id']}")
        payment_refs = sorted({ref for ref in (payment_ref, *other_refs) if ref})
        # Sorted, as the refund side takes them, so the two never wait on
        # each other in a cycle.
        for ref in payment_refs:
            _payment_lock(conn, ref)
        existing = conn.execute(
            "SELECT id, status, amount, currency FROM affiliate_conversions WHERE order_id = ?",
            (event["order_id"],),
        ).fetchone()
        if existing is not None:
            conn.rollback()
            if (money(existing["amount"]) != event["amount"]
                    or existing["currency"] != event["currency"]):
                raise AffiliateError(
                    409, "order_conflict",
                    t("This order id was already reported with a different amount or currency."))
            return 200, _conversion_result(existing)
        partner_id, link_id, click_id = _attribution(conn, event["ref"])
        status = "pending" if partner_id else "unattributed"
        refunded, refunded_at = _take_held_refund(
            conn, source, [event["order_id"]] if source == "webhook" else payment_refs,
            event["amount"])
        if refunded is not None and refunded >= event["amount"]:
            status = "refunded"
        row = conn.execute(
            """INSERT INTO affiliate_conversions
                      (order_id, source, status, amount, currency, occurred_at, ref,
                       partner_id, link_id, click_id, body_sha256, payment_ref,
                       refunded_amount, refunded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               RETURNING id, status""",
            (event["order_id"], source, status, event["amount"], event["currency"],
             event["occurred_at"], event["ref"], partner_id, link_id, click_id,
             body_sha256, payment_ref, refunded, refunded_at),
        ).fetchone()
        conn.commit()
        return 200, _conversion_result(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_refund(event):
    """Apply a refund the shop reports; return (status code, response body).

    `amount`, when given, is the total refunded so far. A full refund before
    approval ends the conversion (nothing was paid or created); after approval
    it is flagged for the administrator, who decides. A partial refund is only
    recorded: no integration ever changes an amount on its own.
    """
    conn = get_conn()
    try:
        conn.begin()
        _lock(conn, f"affiliate-order:{event['order_id']}")
        row = conn.execute(
            """SELECT id, status, amount, refunded_amount FROM affiliate_conversions
                WHERE order_id = ? FOR UPDATE""",
            (event["order_id"],),
        ).fetchone()
        nothing = event["amount"] is not None and event["amount"] == 0
        if row is None:
            # Deliveries are not ordered: keep the refund for its order rather
            # than acknowledge it and lose it, which would let that order be
            # approved (even automatically) as if nothing had been refunded.
            if not nothing:
                _hold_refund(conn, "webhook", event["order_id"], amount=event["amount"])
            conn.commit()
            return 200, {"status": "refund_before_order", "conversion_id": None}
        order_amount = money(row["amount"])
        refunded = order_amount if event["amount"] is None else min(event["amount"], order_amount)
        previous = None if row["refunded_amount"] is None else money(row["refunded_amount"])
        if previous is not None:
            refunded = max(refunded, previous)
        if refunded == 0 or refunded == previous:
            # Nothing new was refunded (a refund of nothing, or the same total
            # sent again): it must not flag an approved order or make a pending
            # one wait for a person.
            conn.rollback()
            return 200, _conversion_result(row)
        full = refunded >= order_amount
        status = row["status"]
        if status in ("pending", "unattributed") and full:
            status = "refunded"
        elif status == "approved":
            status = "refund_after_approval"
        if status in ("rejected", "refunded") and row["status"] == status:
            conn.rollback()
            return 200, _conversion_result(row)
        conn.execute(
            """UPDATE affiliate_conversions
                  SET status = ?, refunded_amount = ?,
                      refunded_at = COALESCE(refunded_at, now())
                WHERE id = ?""",
            (status, refunded, row["id"]),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    if status == "refund_after_approval" and row["status"] == "approved":
        _notify_admins_of_late_refund(event["order_id"])
    return 200, {"status": status, "conversion_id": row["id"]}


def _notify_admins_of_late_refund(order_id):
    from api import notifications as notif
    try:
        notif.notify_all_admins(
            "warning",
            t("Refund after approval: order {order}", order=order_id),
            t("Check the order under Affiliate and reverse the commission if it is unpaid."),
            "affiliate.html?status=refund_after_approval",
            title_key="ntf_affiliate_refund_title",
            body_key="ntf_affiliate_refund_body",
            params={"order": order_id},
        )
    except Exception as exc:
        logger.warning("affiliate_refund_notification_failed type=%s", type(exc).__name__)


def _visitor_key(address):
    """The sender an address stands for: an IPv4 address itself, an IPv6
    one by its /64 network, because a single IPv6 host normally holds a
    whole /64 and can change its source address at will."""
    try:
        ip = ipaddress.ip_address(address or "")
    except ValueError:
        return "unknown"
    if ip.version == 6 and ip.ipv4_mapped is not None:
        return str(ip.ipv4_mapped)
    if ip.version == 6:
        return str(ipaddress.IPv6Network((int(ip) >> 64 << 64, 64)))
    return str(ip)


def _visitor_bucket(address, door="click"):
    """A per-visitor rate bucket for one door that does not keep the IP
    address itself."""
    material = config_secret("PARTNERTYPE_SESSION_SECRET", True).encode()
    digest = hmac.new(material, _visitor_key(address).encode(), hashlib.sha256)
    return f"affiliate:{door}:" + digest.hexdigest()[:32]


def shop_address(shop_url, code, click_id=None):
    """The shop URL with pt_ref (and pt_click, when a click was counted)
    added to its own query."""
    parts = urlsplit(shop_url)
    query = [(name, value) for name, value in parse_qsl(parts.query, keep_blank_values=True)
             if name not in ("pt_ref", "pt_click")]
    query.append(("pt_ref", code))
    if click_id:
        query.append(("pt_click", click_id))
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", urlencode(query), ""))


def follow_link(code, address):
    """Count one click on a live link; return the shop address to send the
    visitor to, or None when the link does not work (any reason, one answer).

    The code is the only input: nothing else from the request reaches the
    address, so the redirect can never point anywhere but the shop.
    """
    if not isinstance(code, str) or not re.fullmatch(r"[a-z0-9]{10}", code):
        return None
    with system_context("affiliate link redirect"):
        settings = current_settings()
        if settings[KEY_ENABLED] != "1":
            return None
        try:
            shop_url = parse_shop_url(settings[KEY_SHOP_URL])
        except ValueError:
            return None
        if not shop_url:
            return None
        # The limits stop inflated click counts, never a visit: many real
        # buyers share one address (mobile carriers, offices, public Wi-Fi),
        # and the shop address is fixed, so over a limit the visitor is still
        # sent on and only the click goes uncounted. The visitor's own limit
        # comes first, so one sender over it never spends the instance-wide
        # budget that every buyer shares.
        from api.gateway import limits
        counted, _ = limits.consume(_visitor_bucket(address), CLICKS_PER_MINUTE_PER_VISITOR, 60)
        if counted:
            counted, _ = limits.consume("affiliate:clicks", CLICKS_PER_MINUTE, 60)
        with get_conn() as conn:
            link = conn.execute(
                """SELECT l.id, l.partner_id FROM affiliate_links l
                     JOIN users u ON u.id = l.partner_id
                    WHERE l.code = ? AND l.revoked_at IS NULL
                      AND u.active = 1 AND u.role = 'partner'""",
                (code,)).fetchone()
            if link is None:
                return None
            if not counted:
                # The code alone still attributes the order.
                return shop_address(shop_url, code)
            click_id = secrets.token_urlsafe(16)
            conn.execute(
                "INSERT INTO affiliate_clicks (click_id, link_id, partner_id) VALUES (?, ?, ?)",
                (click_id, link["id"], link["partner_id"]))
        return shop_address(shop_url, code, click_id)


def sweep_clicks(conn):
    """Delete clicks past their retention; called by the daily maintenance."""
    return conn.execute(
        "DELETE FROM affiliate_clicks WHERE created_at < now() - make_interval(days => ?)",
        (CLICK_RETENTION_DAYS,)).rowcount


def _rate_limit(bucket, limit):
    from api.gateway import limits
    allowed, retry_after = limits.consume(bucket, limit, 60)
    if not allowed:
        raise AffiliateError(429, "rate_limited", t("Too many requests. Try again shortly."),
                             headers={"Retry-After": str(retry_after)})


def _signed_door(door, headers, header_name, body, address):
    """The checks every shop door shares, in a deliberate order; raises the
    refusal or returns once the body is known to come from the shop.

    Only a request that fails the signature check counts against its sender,
    and the door's own shared limit is spent only by signed ones. Shops often
    deliver from addresses many other customers share (Shopify, Zapier, Make),
    so counting signed deliveries per sender would let anyone's unsigned junk
    from the same address lock the real shop out.
    """
    if len(body) > MAX_BODY_BYTES:
        raise AffiliateError(413, "too_large", t("The request body is too large."))
    conn = get_conn()
    try:
        secret = _load_secret(conn, door)
    finally:
        conn.close()
    try:
        verify_signature(headers.get(header_name), body, secret)
    except AffiliateError:
        _rate_limit(_visitor_bucket(address, f"{door}:fail"), WEBHOOK_PER_MINUTE)
        raise
    _rate_limit(f"affiliate:{door}", WEBHOOK_PER_MINUTE)


def receive_webhook(headers, body, address=None, test_event=False):
    """Handle one generic shop webhook; return (status, response body).

    The order of checks is deliberate: a switched-off programme answers 404
    before anything is read, the size is checked before the signature, and the
    body is parsed only once it is known to come from the shop.

    `test_event` is only for the settings screen's own signed ping: it may
    pass while the programme is off, so the administrator can check the
    secret before switching on; anything but a ping is still refused then.
    """
    with system_context("affiliate webhook"):
        settings = current_settings()
        disabled = AffiliateError(404, "affiliate_disabled",
                                  t("The affiliate programme is switched off."))
        enabled = settings[KEY_ENABLED] == "1"
        if not enabled and not test_event:
            raise disabled
        _signed_door("webhook", headers, SIGNATURE_HEADER, body, address)
        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise _bad(N_("The body must be a JSON object."))
        event = parse_event(decoded, settings["currency"])
        if event["event"] == PING:
            return 200, {"status": "ok"}
        if not enabled:
            raise disabled
        body_sha256 = hashlib.sha256(body).hexdigest()
        if event["event"] == ORDER_PAID:
            return record_order(event, body_sha256)
        return record_refund(event)


# ─── STRIPE ──────────────────────────────────────────────────────────────────

def _stripe_amount(value, currency):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _bad(N_("The Stripe event has no valid amount."))
    exponent = 0 if currency in STRIPE_ZERO_DECIMAL else 2
    return (Decimal(value) / (Decimal(10) ** exponent)).quantize(Decimal("0.01"))


def _stripe_order(obj, currency, body):
    """A paid Checkout session; one not paid yet waits for its async event."""
    if obj.get("payment_status") != "paid":
        return 200, {"status": "ignored"}
    metadata = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
    ref = obj.get("client_reference_id") or metadata.get("pt_ref")
    if str(obj.get("currency") or "").upper() != currency:
        # The account sends every sale, so a refusal here would make Stripe
        # retry and in the end disable the endpoint for the sales that count.
        # Without a referral it is simply not ours; with one it is lost
        # commission, worth a line in the log.
        if not ref:
            return 200, {"status": "ignored"}
        logger.warning("affiliate_stripe_unsupported_currency currency=%s",
                       re.sub(r"[^A-Za-z]", "", str(obj.get("currency")))[:3])
        return 200, {"status": "unsupported_currency"}
    session_id = obj.get("id")
    if not isinstance(session_id, str) or not _ORDER_ID_RE.fullmatch(session_id):
        raise _bad(N_("The Stripe event has no valid session id."))
    tax = (obj.get("total_details") or {}).get("amount_tax") or 0
    net = _stripe_amount(obj.get("amount_total"), currency) - _stripe_amount(tax, currency)
    if net < 0:
        raise _bad(N_("The Stripe event has no valid amount."))
    created = obj.get("created")
    now = datetime.now(timezone.utc)
    occurred = (datetime.fromtimestamp(created, timezone.utc)
                if isinstance(created, int) and not isinstance(created, bool)
                and 0 < created <= now.timestamp() + SIGNATURE_TOLERANCE_SECONDS else now)
    payment = obj.get("payment_intent")
    event = {
        "event": ORDER_PAID,
        "order_id": session_id,
        "amount": net,
        "currency": currency,
        "ref": ref if isinstance(ref, str) and _REF_RE.fullmatch(ref) else None,
        "occurred_at": max(occurred, now - timedelta(days=MAX_ORDER_AGE_DAYS)),
    }
    # A subscription session has no payment intent of its own; its first
    # invoice is the only handle a later refund can be matched by.
    invoice = _stripe_id(obj.get("invoice"))
    payment_ref = _stripe_id(payment) or invoice
    return record_order(event, hashlib.sha256(body).hexdigest(), source="stripe",
                        payment_ref=payment_ref, other_refs=(invoice,))


def _stripe_id(value):
    return value if isinstance(value, str) and 0 < len(value) <= 128 else None


def _stripe_refund(obj, currency):
    """A refunded charge, matched to its order through the payment intent
    (or, for a subscription's first payment, the invoice); a partial refund
    is converted to the net amount in proportion.

    The account sends every refund, most of them not for referral sales, so a
    charge that matches no order is acknowledged and ignored. One in the
    instance currency may still be an order that has not arrived yet; it is
    held quietly so that order starts out refunded."""
    refs = sorted({ref for ref in (_stripe_id(obj.get("payment_intent")),
                                   _stripe_id(obj.get("invoice"))) if ref})
    charged, refunded = obj.get("amount"), obj.get("amount_refunded")
    valid = all(isinstance(value, int) and not isinstance(value, bool) and value >= 0
                for value in (charged, refunded))
    if not refs:
        return 200, {"status": "ignored"}
    conn = get_conn()
    try:
        conn.begin()
        for ref in refs:
            _payment_lock(conn, ref)
        row = conn.execute(
            "SELECT order_id, amount FROM affiliate_conversions WHERE payment_ref = ANY(?)",
            (refs,)).fetchone()
        if row is None:
            ours = str(obj.get("currency") or "").upper() == currency
            if ours and valid and refunded > 0:
                for ref in refs:
                    _hold_refund(conn, "stripe", ref, charged=charged, refunded=refunded)
            conn.commit()
            return 200, {"status": "ignored"}
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    if not valid:
        raise _bad(N_("The Stripe event has no valid amount."))
    amount = None
    if charged and refunded < charged:
        amount = (money(row["amount"]) * Decimal(refunded) / Decimal(charged)).quantize(
            Decimal("0.01"))
    return record_refund({"event": ORDER_REFUNDED, "order_id": row["order_id"],
                          "amount": amount})


def receive_stripe(headers, body, address=None):
    """Handle one Stripe event; return (status, response body). Every event
    type other than the three it needs is acknowledged and ignored, so Stripe
    does not retry it."""
    with system_context("affiliate stripe webhook"):
        settings = current_settings()
        if settings[KEY_ENABLED] != "1":
            raise AffiliateError(404, "affiliate_disabled",
                                 t("The affiliate programme is switched off."))
        _signed_door("stripe", headers, STRIPE_SIGNATURE_HEADER, body, address)
        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise _bad(N_("The body must be a JSON object."))
        data = decoded.get("data") if isinstance(decoded, dict) else None
        obj = data.get("object") if isinstance(data, dict) else None
        kind = decoded.get("type") if isinstance(decoded, dict) else None
        if not isinstance(kind, str) or not isinstance(obj, dict):
            raise _bad(N_("The body is not a Stripe event."))
        if kind in STRIPE_PAID_EVENTS:
            return _stripe_order(obj, settings["currency"], body)
        if kind == STRIPE_REFUND_EVENT:
            return _stripe_refund(obj, settings["currency"])
        return 200, {"status": "ignored"}


# ─── DECISIONS ───────────────────────────────────────────────────────────────

MAX_BATCH = 100


def _require_decider(user):
    from api.authz import can_pay_commission
    if not can_pay_commission(user):
        raise PermissionError(t("Only the administrator decides on shop orders."))


def _parse_ids(value):
    if (not isinstance(value, list) or not value or len(value) > MAX_BATCH
            or any(isinstance(item, bool) or not isinstance(item, int) or item <= 0
                   for item in value)):
        raise ValueError(t("Choose between 1 and {max} orders.", max=MAX_BATCH))
    return list(dict.fromkeys(value))


def _positive_int(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _parse_id(value):
    number = _positive_int(value)
    if number is None:
        raise ValueError(t("Invalid order."))
    return number


def _locked(conn, ids):
    rows = conn.execute(
        "SELECT * FROM affiliate_conversions WHERE id = ANY(?) ORDER BY id FOR UPDATE",
        (ids,),
    ).fetchall()
    return {row["id"]: row for row in rows}


def _partner_is_active(conn, partner_id):
    partner = conn.execute(
        "SELECT active, role FROM users WHERE id = ?", (partner_id,)).fetchone()
    return partner is not None and bool(partner["active"]) and partner["role"] == "partner"


def _approve(conn, ids, actor_id, automatic=False):
    """Approve the pending conversions among `ids` inside the caller's
    transaction; return one result per id, in order. An automatic approval
    also leaves alone any order with a refund, which waits for a person."""
    from api import leads
    rows = _locked(conn, ids)
    results = []
    for conversion_id in ids:
        row = rows.get(conversion_id)
        if (row is None or row["status"] != "pending"
                or (automatic and row["refunded_amount"] is not None)):
            results.append({"id": conversion_id, "result": "skipped",
                            "status": row["status"] if row else None})
            continue
        if row["partner_id"] is None or not _partner_is_active(conn, row["partner_id"]):
            # Money follows the link: a partner who was deactivated, erased or
            # deleted after the purchase earns nothing new. The order stays
            # pending, and the list tells the administrator why.
            results.append({"id": conversion_id, "result": "partner_inactive",
                            "status": row["status"]})
            continue
        value = money(row["amount"]) - money(row["refunded_amount"] or 0)
        lead_id, commission = leads.create_affiliate_lead(
            conn, partner_id=row["partner_id"], order_id=row["order_id"],
            value=value, actor_id=actor_id)
        conn.execute(
            """UPDATE affiliate_conversions
                  SET status = 'approved', lead_id = ?, decided_at = now(), decided_by = ?
                WHERE id = ?""",
            (lead_id, actor_id, conversion_id),
        )
        results.append({"id": conversion_id, "result": "approved", "lead_id": lead_id,
                        "commission": commission, "partner_id": row["partner_id"],
                        "order_id": row["order_id"]})
    return results


def _after_approval(results, user, actor_id):
    """Notify and log what an approval committed; never raises."""
    from api import blocks
    from api import notifications as notif
    for item in results:
        if item["result"] != "approved":
            continue
        for effect in (
            lambda: notif.create_notification(
                item["partner_id"], "success",
                t("Purchase approved: order {order}", order=item["order_id"]),
                t("Your commission: {amount}", amount=f"{item['commission']:.2f}"),
                "affiliate.html",
                title_key="ntf_affiliate_approved_title",
                body_key="ntf_affiliate_approved_body",
                params={"order": item["order_id"], "amount": f"{item['commission']:.2f}"}),
            lambda: audit.log(user, "affiliate.approve", "lead", item["lead_id"],
                              f"{item['order_id']} | {item['commission']:.2f}"),
        ):
            try:
                effect()
            except Exception as exc:
                logger.warning("affiliate_effect_failed type=%s", type(exc).__name__)
        blocks.emit("affiliate.conversion", item["lead_id"], actor_id)


def _public_results(results):
    return [{key: value for key, value in item.items() if key != "partner_id"}
            for item in results]


def approve(user, ids):
    """The administrator approves the selected pending orders, all or none."""
    _require_decider(user)
    ids = _parse_ids(ids)
    conn = get_conn()
    try:
        conn.begin()
        results = _approve(conn, ids, user["id"])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    _after_approval(results, user, user["id"])
    return _public_results(results)


@system_context("maintenance: affiliate auto-approval")
def auto_approve():
    """Approve pending orders older than the refund window, when the
    administrator has switched that on. The window starts at the later of
    the shop's order time and its arrival here, so an order reported late
    (or paid late, as with bank debits) still gets the whole window. Orders
    with any refund wait for a person, and so does a Stripe order not tied
    to a payment intent (a subscription's first payment), whose refund newer
    Stripe API versions give no way to match. Returns how many were
    approved."""
    settings = current_settings()
    if settings[KEY_ENABLED] != "1" or settings[KEY_AUTO_APPROVE] != "1":
        return 0
    days = int(settings[KEY_AUTO_APPROVE_DAYS])
    conn = get_conn()
    try:
        conn.begin()
        # Selected and locked in one statement: a refund arriving meanwhile
        # either commits first (and the row no longer matches) or waits, and a
        # row someone else holds is left for the next run.
        ids = [row["id"] for row in conn.execute(
            """SELECT id FROM affiliate_conversions
                WHERE status = 'pending' AND refunded_amount IS NULL
                  AND partner_id IS NOT NULL
                  AND EXISTS (SELECT 1 FROM users u WHERE u.id = partner_id
                                 AND u.active = 1 AND u.role = 'partner')
                  AND (source <> 'stripe' OR left(payment_ref, 3) = 'pi_')
                  AND GREATEST(occurred_at, received_at) <= now() - make_interval(days => ?)
                ORDER BY id LIMIT 1000
                FOR UPDATE SKIP LOCKED""",
            (days,)).fetchall()]
        results = _approve(conn, ids, None, automatic=True) if ids else []
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    _after_approval(results, None, None)
    return sum(1 for item in results if item["result"] == "approved")


def reject(user, ids):
    """The administrator rejects pending or unattributed orders."""
    _require_decider(user)
    ids = _parse_ids(ids)
    # Pooled connections autocommit, so the row lock only holds inside an
    # explicit transaction; without it an approval could land between the
    # check and the update and be overwritten, leaving a payable lead behind.
    conn = get_conn()
    try:
        conn.begin()
        rows = _locked(conn, ids)
        results = []
        for conversion_id in ids:
            row = rows.get(conversion_id)
            if row is None or row["status"] not in ("pending", "unattributed"):
                results.append({"id": conversion_id, "result": "skipped",
                                "status": row["status"] if row else None})
                continue
            conn.execute(
                """UPDATE affiliate_conversions
                      SET status = 'rejected', decided_at = now(), decided_by = ?
                    WHERE id = ? AND status IN ('pending', 'unattributed')""",
                (user["id"], conversion_id),
            )
            results.append({"id": conversion_id, "result": "rejected"})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    for item in results:
        if item["result"] == "rejected":
            audit.log(user, "affiliate.reject", "affiliate_conversion", item["id"], "")
    return results


def assign(user, conversion_id, partner_id):
    """Give an unattributed order to a partner; it then waits for approval."""
    _require_decider(user)
    conversion_id = _parse_id(conversion_id)
    if isinstance(partner_id, bool) or not isinstance(partner_id, int) or partner_id <= 0:
        raise ValueError(t("Choose an active partner."))
    not_unattributed = AffiliateError(409, "not_unattributed",
                                      t("Only an order without a partner can be assigned."))
    # A real transaction, as in reject(): a rejected or refunded order must
    # never come back to life as pending (and then be auto-approved).
    conn = get_conn()
    try:
        conn.begin()
        row = _locked(conn, [conversion_id]).get(conversion_id)
        if row is None:
            raise AffiliateError(404, "not_found", t("Order not found."))
        if row["status"] != "unattributed":
            raise not_unattributed
        partner = conn.execute(
            "SELECT id, active, role FROM users WHERE id = ?", (partner_id,)).fetchone()
        if partner is None or not partner["active"] or partner["role"] != "partner":
            raise ValueError(t("Choose an active partner."))
        changed = conn.execute(
            """UPDATE affiliate_conversions SET status = 'pending', partner_id = ?
                WHERE id = ? AND status = 'unattributed'""",
            (partner_id, conversion_id)).rowcount
        if changed == 0:
            raise not_unattributed
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    audit.log(user, "affiliate.assign", "affiliate_conversion", conversion_id,
              f"{row['order_id']} → {partner_id}")
    return {"id": conversion_id, "status": "pending", "partner_id": partner_id}


def reverse(user, conversion_id):
    """Undo the unpaid commission of an order refunded after approval."""
    from api import leads
    _require_decider(user)
    conversion_id = _parse_id(conversion_id)
    conn = get_conn()
    try:
        conn.begin()
        row = conn.execute(
            "SELECT * FROM affiliate_conversions WHERE id = ? FOR UPDATE",
            (conversion_id,)).fetchone()
        if row is None:
            raise AffiliateError(404, "not_found", t("Order not found."))
        if row["status"] != "refund_after_approval" or not row["lead_id"]:
            raise AffiliateError(409, "not_refunded",
                                 t("Only an approved order that was refunded can be reversed."))
        if money(row["refunded_amount"] or 0) < money(row["amount"]):
            raise AffiliateError(
                409, "partial_refund",
                t("Only part of this order was refunded. Correct the lead's commission by hand "
                  "if needed."))
        try:
            partner_id, commission = leads.reverse_affiliate_lead(
                conn, row["lead_id"], user["id"])
        except leads.LeadWorkflowConflict as exc:
            raise AffiliateError(409, exc.code, str(exc))
        conn.execute(
            """UPDATE affiliate_conversions
                  SET status = 'reversed', decided_at = now(), decided_by = ?
                WHERE id = ?""",
            (user["id"], conversion_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    audit.log(user, "affiliate.reverse", "lead", row["lead_id"],
              f"{row['order_id']} | {commission or 0:.2f}")
    return {"id": conversion_id, "status": "reversed", "lead_id": row["lead_id"]}


# ─── HTTP ────────────────────────────────────────────────────────────────────

def _json_response(status, payload, headers=None):
    from flask import Response
    return Response(json.dumps(payload, ensure_ascii=False), status=status,
                    headers=headers or {}, content_type="application/json; charset=utf-8")


def _error_response(exc):
    return _json_response(exc.status, {"error": exc.message, "code": exc.code}, exc.headers)


def register(app, config):
    """The shop-facing doors. They carry no session cookie, so they live
    outside the cookie router (which refuses a POST without a browser origin)
    and must be registered before its /api/<path> rule."""
    from flask import request
    from api.i18n import language_from_header, use_language

    def affiliate_webhook():
        with use_language(language_from_header(request.headers.get("Accept-Language"))):
            declared = request.content_length
            if declared is not None and declared > MAX_BODY_BYTES:
                return _error_response(AffiliateError(
                    413, "too_large", t("The request body is too large.")))
            try:
                status, payload = receive_webhook(request.headers, request.get_data(),
                                                  request.remote_addr)
            except AffiliateError as exc:
                return _error_response(exc)
            return _json_response(status, payload)

    def affiliate_redirect(code):
        from flask import Response
        target = follow_link(code, request.remote_addr)
        if target is None:
            return Response(status=404)
        return Response(status=302, headers={"Location": target, "Cache-Control": "no-store"})

    def affiliate_stripe():
        with use_language(language_from_header(request.headers.get("Accept-Language"))):
            declared = request.content_length
            if declared is not None and declared > MAX_BODY_BYTES:
                return _error_response(AffiliateError(
                    413, "too_large", t("The request body is too large.")))
            try:
                status, payload = receive_stripe(request.headers, request.get_data(),
                                                 request.remote_addr)
            except AffiliateError as exc:
                return _error_response(exc)
            return _json_response(status, payload)

    app.add_url_rule("/api/affiliate/webhook", "affiliate_webhook",
                     affiliate_webhook, methods=["POST"])
    app.add_url_rule("/api/affiliate/stripe", "affiliate_stripe",
                     affiliate_stripe, methods=["POST"])
    app.add_url_rule("/r/<code>", "affiliate_redirect", affiliate_redirect, methods=["GET"])


# ─── PARTNER AND ADMIN API (cookie router) ───────────────────────────────────

def my_summary(user):
    """The partner's own link and what it brought, for his dashboard."""
    from api import leads
    settings = current_settings()
    conn = get_conn()
    try:
        link = _live_link(conn, user["id"])
        clicks = conn.execute(
            "SELECT count(*) AS n FROM affiliate_clicks WHERE partner_id = ?",
            (user["id"],)).fetchone()["n"]
        rows = conn.execute(
            """SELECT c.status, c.amount, c.refunded_amount, l.commission
                 FROM affiliate_conversions c
                 LEFT JOIN leads l ON l.id = c.lead_id
                WHERE c.partner_id = ?
                  AND c.status IN ('pending', 'approved', 'refund_after_approval')""",
            (user["id"],)).fetchall()
    finally:
        conn.close()
    pending = [row for row in rows if row["status"] == "pending"]
    pending_commission = sum(
        Decimal(str(leads.affiliate_commission_forecast(
            user["id"], money(row["amount"]) - money(row["refunded_amount"] or 0))))
        for row in pending)
    approved_commission = sum(
        money(row["commission"] or 0) for row in rows if row["status"] != "pending")
    from api.config import Config
    link = dict(link) if link else None
    if link:
        link["url"] = f"{Config.load().base_url}/r/{link['code']}"
    return {
        "enabled": settings[KEY_ENABLED] == "1",
        "link": link,
        "clicks": clicks,
        "purchases": len(rows),
        "pending_count": len(pending),
        "pending_commission": f"{money(pending_commission):.2f}",
        "approved_commission": f"{money(approved_commission):.2f}",
        "currency": settings["currency"],
    }


PAGE_SIZE = 50


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def list_conversions(user, filters):
    """Orders for the producer's staff, newest first, with the commission
    each one earned (approved) or would earn (pending)."""
    from api import gdpr, leads
    from api.authz import is_staff
    if not is_staff(user):
        raise PermissionError(t("Access denied."))
    status = filters.get("status")
    if status is not None and status not in CONVERSION_STATUSES:
        raise ValueError(t("Unknown order status."))
    partner_id = filters.get("partner_id")
    if partner_id is not None:
        partner_id = _positive_int(partner_id)
        if partner_id is None:
            raise ValueError(t("Invalid partner."))
    page = filters.get("page") or "1"
    page = int(page) if str(page).isdigit() and 0 < int(page) <= 10000 else 1
    where, params = [], []
    if status:
        where.append("c.status = ?")
        params.append(status)
    if partner_id:
        where.append("c.partner_id = ?")
        params.append(partner_id)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    conn = get_conn()
    try:
        total = conn.execute(
            f"SELECT count(*) AS n FROM affiliate_conversions c {clause}", params
        ).fetchone()["n"]
        rows = conn.execute(
            f"""SELECT c.id, c.order_id, c.source, c.status, c.amount, c.refunded_amount,
                       c.currency, c.occurred_at, c.received_at, c.partner_id,
                       u.name AS partner_name, u.email AS partner_email,
                       (u.active = 1 AND u.role = 'partner') AS partner_active,
                       c.lead_id, c.decided_at,
                       l.commission, l.commission_paid_at
                  FROM affiliate_conversions c
                  LEFT JOIN users u ON u.id = c.partner_id
                  LEFT JOIN leads l ON l.id = c.lead_id
                  {clause}
                 ORDER BY c.received_at DESC, c.id DESC
                 LIMIT ? OFFSET ?""",
            params + [PAGE_SIZE, (page - 1) * PAGE_SIZE],
        ).fetchall()
    finally:
        conn.close()
    items = []
    for row in rows:
        item = dict(row)
        # Which kind of "not active" decides what the screen may suggest: an
        # erased account must never be reactivated to be paid. None: the order
        # has no partner (never had one, or the account was deleted).
        email = item.pop("partner_email")
        if row["partner_id"] is None:
            partner_state = None
        elif email == gdpr.erased_email(row["partner_id"]):
            partner_state = "erased"
        else:
            partner_state = "active" if row["partner_active"] else "inactive"
        net = money(row["amount"]) - money(row["refunded_amount"] or 0)
        if row["status"] == "pending":
            # A deleted partner leaves a pending order with nobody to earn on it.
            commission = (None if row["partner_id"] is None
                          else leads.affiliate_commission_forecast(row["partner_id"], net))
        else:
            commission = row["commission"]
        item.update({
            "amount": f"{money(row['amount']):.2f}",
            "refunded_amount": (None if row["refunded_amount"] is None
                                else f"{money(row['refunded_amount']):.2f}"),
            "commission": None if commission is None else f"{money(commission):.2f}",
            "commission_paid": bool(row["commission_paid_at"]),
            "partner_active": bool(row["partner_active"]),
            "partner_state": partner_state,
            "occurred_at": _iso(row["occurred_at"]),
            "received_at": _iso(row["received_at"]),
            "decided_at": _iso(row["decided_at"]),
        })
        item.pop("commission_paid_at")
        items.append(item)
    return {"items": items, "page": page, "per_page": PAGE_SIZE, "total": total}


def _route_my_link(user, groups, qs, body):
    ensure_link(user)
    return 200, my_summary(user)


def _route_me(user, groups, qs, body):
    return 200, my_summary(user)


def read_orders(user, status=None, limit=20):
    """Orders the user may see, newest first, for the AI assistant and the
    gateway. Reading only: no decision is ever made through here."""
    from api import leads
    role = (user or {}).get("role")
    if role not in ("admin", "manager", "partner"):
        raise PermissionError(t("Access denied."))
    if status is not None and status not in CONVERSION_STATUSES:
        raise ValueError(t("Unknown order status."))
    limit = max(1, min(int(limit), PAGE_SIZE))
    if role != "partner":
        filters = {"status": status} if status else {}
        return list_conversions(user, filters)["items"][:limit]
    where = "WHERE c.partner_id = ?" + (" AND c.status = ?" if status else "")
    params = [user["id"]] + ([status] if status else [])
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""SELECT c.id, c.order_id, c.status, c.amount, c.refunded_amount, c.currency,
                       c.occurred_at, l.commission
                  FROM affiliate_conversions c
                  LEFT JOIN leads l ON l.id = c.lead_id
                  {where}
                 ORDER BY c.received_at DESC, c.id DESC LIMIT ?""",
            params + [limit]).fetchall()
    finally:
        conn.close()
    items = []
    for row in rows:
        net = money(row["amount"]) - money(row["refunded_amount"] or 0)
        commission = (leads.affiliate_commission_forecast(user["id"], net)
                      if row["status"] == "pending" else row["commission"])
        items.append({
            "id": row["id"], "order_id": row["order_id"], "status": row["status"],
            "amount": f"{money(row['amount']):.2f}", "currency": row["currency"],
            "occurred_at": _iso(row["occurred_at"]),
            "commission": None if commission is None else f"{money(commission):.2f}",
        })
    return items


def _route_stripe_secret(user, groups, qs, body):
    return 200, set_stripe_secret(user, body.get("secret"))


def _first(qs):
    return {name: values[0] for name, values in (qs or {}).items() if values}


def _route_conversions(user, groups, qs, body):
    return 200, list_conversions(user, _first(qs))


def _route_approve(user, groups, qs, body):
    return 200, {"results": approve(user, body.get("ids"))}


def _route_reject(user, groups, qs, body):
    return 200, {"results": reject(user, body.get("ids"))}


def _route_assign(user, groups, qs, body):
    return 200, assign(user, groups["id"], body.get("partner_id"))


def _route_reverse(user, groups, qs, body):
    return 200, reverse(user, groups["id"])


def _route_settings(user, groups, qs, body):
    return 200, settings_view(user)


def _route_settings_update(user, groups, qs, body):
    return 200, update_settings(user, body)


def _route_secret(user, groups, qs, body):
    return 200, create_webhook_secret(user)


def _route_test_event(user, groups, qs, body):
    return 200, send_test_event(user)


_ROUTES = {
    "affiliate_settings": _route_settings,
    "affiliate_settings_update": _route_settings_update,
    "affiliate_secret": _route_secret,
    "affiliate_test_event": _route_test_event,
    "affiliate_stripe_secret": _route_stripe_secret,
    "affiliate_me": _route_me,
    "affiliate_my_link": _route_my_link,
    "affiliate_conversions": _route_conversions,
    "affiliate_approve": _route_approve,
    "affiliate_reject": _route_reject,
    "affiliate_assign": _route_assign,
    "affiliate_reverse": _route_reverse,
}


def handle_route(action, user, groups, qs, body):
    """Run one routed affiliate action; refusals become their status codes."""
    from api.leads import LeadWorkflowConflict
    try:
        return _ROUTES[action](user, groups, qs, body)
    except LeadWorkflowConflict as exc:
        return 409, {"error": str(exc), "code": exc.code}
    except AffiliateError as exc:
        return exc.status, {"error": exc.message, "code": exc.code}
    except PermissionError as exc:
        return 403, {"error": str(exc)}
    except ValueError as exc:
        return 400, {"error": str(exc)}
