"""
PartnerType — Email Service
Configurable SMTP email sending.
Falls back gracefully when SMTP is not configured (logs to console).
"""
import smtplib
import os
import ssl
import contextvars
from concurrent.futures import ThreadPoolExecutor
from html import escape
import logging
import uuid
from email.utils import formataddr
from pathlib import Path
from api import email_layout
from api.branding import logo as _producer_logo
from api.config import secret
from api.settings_api import get
from api.validation import (
    parse_email,
    parse_integer_id,
    parse_optional_string,
    parse_required_string,
)
from api.i18n import N_, current_language, t


logger = logging.getLogger(__name__)


def _get_smtp_config():
    """Mail is the operator's, not the producer's.

    One transactional mailbox serves every producer on the server. The operator
    sets it once per environment (instances/<env>/mail.json and mail_password)
    and tools/instance.py hands it to each instance as environment variables, so
    no settings screen asks a producer for mail server credentials.
    """
    if os.environ.get("PARTNERTYPE_ENV") in ("development", "test", "staging"):
        return {"host": "mail", "port": 1025, "user": "", "passwd": "",
                "from": "noreply@partnertype.test", "enabled": True, "capture": True}
    host = parse_optional_string(os.environ.get("PARTNERTYPE_SMTP_HOST", ""), N_("SMTP host"), max_length=253)
    sender = os.environ.get("PARTNERTYPE_SMTP_FROM", "").strip()
    return {
        "host":    host,
        "port":    parse_integer_id(os.environ.get("PARTNERTYPE_SMTP_PORT") or 587, N_("SMTP port"), maximum=65535),
        "user":    os.environ.get("PARTNERTYPE_SMTP_USER", ""),
        "passwd":  secret("PARTNERTYPE_SMTP_PASSWORD"),
        # No default sender: a made-up address on a domain the operator does not
        # own fails SPF/DMARC at best and impersonates someone at worst.
        "from":    (parse_email(sender, N_("SMTP sender")) if sender else ""),
        "enabled": bool(host),
    }


def _sender(address: str) -> str:
    """The producer's name on the From line, the shared mailbox behind it.

    A partner should see who invited them, not the name of the platform. The
    company name is the producer's own text, so line breaks are dropped before
    formataddr encodes it -- it can never add a header.
    """
    name = " ".join(str(get("company_name") or "").split())
    return formataddr((name, address)) if name else address


def send(to: str, subject: str, html: str, text: str = "") -> dict:
    """
    Send an email. Returns {"sent": True} or {"sent": False, "reason": "..."}.
    If SMTP is not enabled, logs to stdout and returns sent=False.
    """
    try:
        to = parse_email(to, N_("Recipient address"))
        subject = parse_required_string(subject, N_("Subject"), max_length=200)
        if "\r" in subject or "\n" in subject:
            raise ValueError(t("The subject contains characters that are not allowed."))
        html = parse_required_string(html, N_("HTML body"), max_length=100000)
        text = parse_optional_string(text, N_("Text body"), max_length=100000)
        cfg = _get_smtp_config()

        if not cfg["enabled"] or not cfg["host"]:
            logger.info("smtp_disabled")
            return {"sent": False, "reason": "smtp_disabled"}
        if not cfg["from"]:
            logger.error("smtp_sender_not_configured: PARTNERTYPE_SMTP_HOST is set but "
                         "PARTNERTYPE_SMTP_FROM is not; no mail is sent until it is")
            return {"sent": False, "reason": "sender_not_configured"}

        msg = email_layout.message(subject, _sender(cfg["from"]), to, text, html, _logo())

        context = ssl.create_default_context()
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=10) as server:
            server.ehlo()
            if not cfg.get("capture"):
                server.starttls(context=context)
            if cfg["user"]:
                server.login(cfg["user"], cfg["passwd"])
            server.sendmail(cfg["from"], [to], msg.as_string())

        return {"sent": True}

    except ValueError:
        raise
    except Exception as exc:
        request_id = uuid.uuid4().hex
        logger.error(
            "smtp_error request_id=%s exception=%s",
            request_id,
            type(exc).__name__,
        )
        return {"sent": False, "reason": "smtp_error", "request_id": request_id}


# ─── OFF THE REQUEST THREAD ───────────────────────────────────────────────────

# A few workers are plenty: the mail sent this way is rare and rate-limited
# upstream, and a bounded pool keeps a flood of requests from turning into a
# flood of threads.
_BACKGROUND = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mail")


def run_in_background(task, *args):
    """Run `task(*args)` after the response has gone out.

    For public forms whose answer must not depend on whether an account
    exists: work that happens only for a real account (a database write, an
    SMTP conversation that can take seconds) would otherwise show up in the
    response time. Failures are logged by class name only, like every other
    mail failure here.
    """
    def _guarded():
        try:
            task(*args)
        except Exception as exc:
            logger.error("background_task_failed exception=%s", type(exc).__name__)

    # The task keeps the database context of the request that queued it
    # (row-level security): a worker thread starts with none, and a mail
    # task run as nobody would read nothing.
    return _BACKGROUND.submit(contextvars.copy_context().run, _guarded)


# ─── TEMPLATES ────────────────────────────────────────────────────────────────

_DEFAULT_LOGO = Path(__file__).resolve().parents[1] / "img" / "logo-light-128.png"


def _logo():
    """(bytes, subtype) for the header of every mail: the producer's logo when
    they uploaded one (api/branding.py), PartnerType's mark otherwise."""
    try:
        return _producer_logo() or (_DEFAULT_LOGO.read_bytes(), "png")
    except OSError:
        return None


def _base_html(title: str, content: str) -> str:
    """The shared layout (api/email_layout.py) under the producer's name.

    The partner joins the producer's programme, so the header carries the
    producer's company name; the footer says who delivers the mail. Every
    mail is written in the language of the request that sends it (api/i18n.py).
    """
    company = " ".join(str(get("company_name") or "").split())
    footer = (t("Message sent automatically by PartnerType on behalf of {company}.", company=company)
              if company else t("Message sent automatically by PartnerType."))
    return email_layout.render(title, content, sender_name=company or "PartnerType",
                               footer=footer, has_logo=_logo() is not None,
                               lang=current_language())


_BADGE = {
    "success": ("#e6f6ec", "#1c7a43"),
    "error":   ("#fdecea", "#b42318"),
    "warning": ("#fff3e0", "#9d3a00"),
}


def _badge(label: str, kind: str) -> str:
    background, colour = _BADGE.get(kind, _BADGE["warning"])
    return (f'<span style="display:inline-block;padding:3px 10px;font-size:12px;font-weight:600;'
            f'background:{background};color:{colour}">{label}</span>')


def _rows(rows) -> str:
    cells = "".join(
        f'<tr><td style="padding:8px 0;color:{email_layout.FAINT};width:40%">{name}</td>'
        f'<td style="padding:8px 0;font-weight:600;color:{email_layout.INK}">{value}</td></tr>'
        for name, value in rows)
    return (f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" '
            f'style="border-collapse:collapse;margin:8px 0 16px;border-top:1px solid {email_layout.LINE}">{cells}</table>')


def _hello(name_safe: str) -> str:
    return "<p>" + t("Hello <strong>{name}</strong>,", name=name_safe) + "</p>"


def send_lead_created(partner_email: str, partner_name: str, lead_id: str, client_name: str) -> dict:
    partner_name = escape(str(partner_name))
    lead_id = escape(str(lead_id))
    client_name = escape(str(client_name))
    content = f"""
    {_hello(partner_name)}
    <p>{t("Your new lead was accepted into the system.")}</p>
    {_rows([("Lead ID", lead_id), (t("Client"), client_name),
            (t("Status"), _badge(t("Pending"), "warning"))])}
    <p>{t("You will be notified when the status of the lead changes.")}</p>
    """
    return send(
        partner_email,
        t("New lead accepted — {lead}", lead=lead_id),
        _base_html(t("New lead accepted"), content),
        t("Lead {lead} ({client}) accepted into the system.", lead=lead_id, client=client_name),
    )


_STATUS_BADGES = {
    "accepted":    (N_("Accepted"),    "success"),
    "rejected":    (N_("Rejected"),    "error"),
    "in_progress": (N_("In progress"), "warning"),
    "pending":     (N_("Pending"),     "warning"),
}


def send_lead_status_changed(partner_email: str, partner_name: str,
                              lead_id: str, client_name: str,
                              status: str, commission=None) -> dict:
    partner_name = escape(str(partner_name))
    lead_id = escape(str(lead_id))
    client_name = escape(str(client_name))
    if status in _STATUS_BADGES:
        label, badge = _STATUS_BADGES[status]
        label = t(label)
    else:
        label, badge = escape(str(status)), "warning"
    rows = [("Lead ID", lead_id), (t("Client"), client_name),
            (t("New status"), _badge(label, badge))]
    if commission:
        rows.append((t("Commission"), f"{commission:,.0f} PLN"))
    content = f"""
    {_hello(partner_name)}
    <p>{t("The status of your lead has changed.")}</p>
    {_rows(rows)}
    """
    return send(
        partner_email,
        t("Lead {lead} — status: {status}", lead=lead_id, status=label),
        _base_html(t("Lead status change"), content),
        t("Lead {lead} changed its status to {status}.", lead=lead_id, status=label),
    )


def send_partner_invitation(
    partner_email: str, partner_name: str, inviter_name: str, invite_url: str
) -> dict:
    """Send the one-time set-password link to an invited partner.

    Never raises on a transport failure -- ``send`` already converts those
    into a machine-readable reason, and ``api/invitations.py::handle_create``
    always returns the link in the response regardless of this outcome, so
    the producer can still pass it on by hand on a fresh install where SMTP
    is unconfigured.
    """
    partner_name_safe = escape(str(partner_name))
    inviter_name_safe = escape(str(inviter_name))
    content = f"""
    {_hello(partner_name_safe)}
    <p>{t("<strong>{inviter}</strong> invites you to their partner programme.", inviter=inviter_name_safe)}</p>
    <p>{t("To finish registering, set your own password with the link below. The link is valid for 7 days.")}</p>
    {email_layout.button(t("Set a password and activate the account"), str(invite_url))}
    """
    text = t("{name} — {inviter} invites you to their partner programme. "
             "Set your own password at: {url} (link valid for 7 days).",
             name=partner_name, inviter=inviter_name, url=invite_url)
    return send(
        partner_email,
        # The partner joins the PRODUCER's programme; "the PartnerType partner
        # programme" read like an invitation from us.
        t("{inviter} invites you to their partner programme", inviter=inviter_name),
        _base_html(t("Partner invitation"), content),
        text,
    )


def send_password_reset(to: str, name: str, reset_url: str) -> dict:
    """Send the one-time recovery link.

    The mail says plainly that an unrequested message can be ignored: the link
    expires by itself, and saying so stops a recipient from "fixing" a reset
    they never asked for.
    """
    name_safe = escape(str(name))
    content = f"""
    {_hello(name_safe)}
    <p>{t("We received a request to set a new password for your PartnerType account.")}</p>
    <p>{t("The link is valid for one hour and can be used only once.")}</p>
    {email_layout.button(t("Set a new password"), str(reset_url))}
    <p style="margin:20px 0 0;font-size:13px;color:#7a8290">{t("If you did not ask for this change, ignore this message and your password stays the same.")}</p>
    """
    text = t("{name} — we received a request to set a new PartnerType password. "
             "Set it at: {url} (link valid for one hour, single use). "
             "If it was not you, ignore this message.", name=name, url=reset_url)
    return send(
        to,
        t("Setting a new PartnerType password"),
        _base_html(t("New password"), content),
        text,
    )


def send_existing_account_notice(to: str, name: str, login_url: str) -> dict:
    """Tell the owner of an address that somebody tried to register it again.

    The registration form answers the same way for a new and a taken address,
    so the person who simply forgot they already have an account learns it
    here, in their own mailbox, and nobody else learns it at all.
    """
    name_safe = escape(str(name))
    content = f"""
    {_hello(name_safe)}
    <p>{t("Somebody tried to create a new partner account with this e-mail address, but an account with this address already exists.")}</p>
    <p>{t("If it was you, sign in. If you do not remember the password, set a new one from the sign-in screen.")}</p>
    {email_layout.button(t("Go to sign-in"), str(login_url))}
    <p style="margin:20px 0 0;font-size:13px;color:#7a8290">{t("If it was not you, ignore this message and your account stays the same.")}</p>
    """
    text = t("{name} — somebody tried to create a new partner account with this address, but "
             "the account already exists. If it was you, sign in: {url} (you can set a new "
             "password from the sign-in screen). If not, ignore this message.",
             name=name, url=login_url)
    return send(
        to,
        t("An account with this address already exists"),
        _base_html(t("The account already exists"), content),
        text,
    )


def send_team_invitation(
    to: str, name: str, inviter_name: str, invite_url: str
) -> dict:
    """Send the one-time set-password link to an invited team member.

    Mirrors send_partner_invitation's structure and return contract exactly
    -- same works-without-mail link-always-returned behaviour -- but names
    the role the owner uses for it, "channel manager", never "partner".
    """
    name_safe = escape(str(name))
    inviter_name_safe = escape(str(inviter_name))
    content = f"""
    {_hello(name_safe)}
    <p>{t("<strong>{inviter}</strong> invites you to the team as a partner channel manager in PartnerType.", inviter=inviter_name_safe)}</p>
    <p>{t("To finish registering, set your own password with the link below. The link is valid for 7 days.")}</p>
    {email_layout.button(t("Set a password and activate the account"), str(invite_url))}
    """
    text = t("{name} — {inviter} invites you to the team as a partner channel manager "
             "in PartnerType. Set your own password at: {url} (link valid for 7 days).",
             name=name, inviter=inviter_name, url=invite_url)
    return send(
        to,
        t("Invitation to the PartnerType team — channel manager"),
        _base_html(t("Team invitation"), content),
        text,
    )


def send_test(to: str) -> dict:
    content = f"""
    <p>{t("This is a test message from PartnerType.")}</p>
    <p>{t("If you can see this message, the SMTP configuration works.")}</p>
    """
    return send(to, t("SMTP configuration test — PartnerType"),
                _base_html(t("SMTP test"), content), t("SMTP test — PartnerType works!"))
