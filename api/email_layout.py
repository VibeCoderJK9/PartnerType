"""One look for every e-mail we send: each producer's system's
(api/email_service.py) and any sign-up pages built on top of it.

Mail clients are not browsers. Gmail drops a <style> block in some views and
Outlook ignores most of CSS, so the layout is tables with inline styles, the
colours are hex (no oklch, no variables) and the logo travels inside the
message as an attachment referenced by `cid:` -- a linked image is blocked by
default in most clients and would also tell a tracker when the mail was read.
"""
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from api.i18n import t

# The app's Sharp Business palette (css/theme-v1.1.2.css) as hex.
BRAND = '#b55000'
INK = '#12161c'
MUTED = '#565f6e'
FAINT = '#7a8290'
LINE = '#e9ebee'
PAGE = '#f4f5f7'
LOGO_CID = 'logo'
FONT = "Inter,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def button(label, url):
    """A bulletproof button: a padded table cell, so Outlook draws it too."""
    return (f'<table role="presentation" cellspacing="0" cellpadding="0" border="0" style="margin:28px 0 8px">'
            f'<tr><td bgcolor="{BRAND}" style="background:{BRAND}">'
            f'<a href="{escape(url, quote=True)}" style="display:inline-block;padding:14px 28px;font-family:{FONT};'
            f'font-size:15px;font-weight:600;color:#ffffff;text-decoration:none">{escape(label)}</a>'
            f'</td></tr></table>'
            f'<p style="margin:16px 0 0;font-size:13px;line-height:1.5;color:{FAINT}">'
            f'{escape(t("The button does not work? Copy this address into your browser:"))}'
            f'<br><a href="{escape(url, quote=True)}" '
            f'style="color:{BRAND};word-break:break-all">{escape(url)}</a></p>')


def render(heading, body_html, sender_name='PartnerType', footer=None,
           has_logo=True, lang='pl'):
    """The whole HTML document. `body_html` is trusted markup built by the caller."""
    if footer is None:
        footer = t("Message sent automatically.")
    logo = (f'<img src="cid:{LOGO_CID}" width="40" height="40" alt="" '
            f'style="display:block;width:40px;height:40px;border:0">' if has_logo else '')
    return f"""<!DOCTYPE html>
<html lang="{escape(lang, quote=True)}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>{escape(heading)}</title></head>
<body style="margin:0;padding:0;background:{PAGE}">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" bgcolor="{PAGE}" style="background:{PAGE}">
<tr><td align="center" style="padding:32px 16px">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="max-width:560px">
<tr><td style="padding:0 0 20px">
<table role="presentation" cellspacing="0" cellpadding="0" border="0"><tr>
<td style="padding-right:{12 if has_logo else 0}px">{logo}</td>
<td style="font-family:{FONT};font-size:18px;font-weight:600;color:{INK}">{escape(sender_name)}</td>
</tr></table></td></tr>
<tr><td bgcolor="#ffffff" style="background:#ffffff;border:1px solid {LINE};border-top:3px solid {BRAND};padding:36px 36px 32px">
<h1 style="margin:0 0 20px;font-family:{FONT};font-size:22px;line-height:1.3;font-weight:700;color:{INK}">{escape(heading)}</h1>
<div style="font-family:{FONT};font-size:15px;line-height:1.6;color:{MUTED}">{body_html}</div>
</td></tr>
<tr><td style="padding:20px 4px 0;font-family:{FONT};font-size:12px;line-height:1.6;color:{FAINT}">{escape(footer)}</td></tr>
</table></td></tr></table></body></html>"""


def message(subject, sender, to, text, html, logo=None):
    """multipart/related( multipart/alternative(text, html), logo ) -- or plain alternative without a logo."""
    alternative = MIMEMultipart('alternative')
    if text:
        alternative.attach(MIMEText(text, 'plain', 'utf-8'))
    alternative.attach(MIMEText(html, 'html', 'utf-8'))
    if logo:
        outer = MIMEMultipart('related')
        outer.attach(alternative)
        image = MIMEImage(logo[0], _subtype=logo[1])
        image.add_header('Content-ID', f'<{LOGO_CID}>')
        image.add_header('Content-Disposition', 'inline', filename=f'logo.{logo[1]}')
        outer.attach(image)
    else:
        outer = alternative
    outer['Subject'] = subject
    outer['From'] = sender
    outer['To'] = to
    return outer
