"""The producer's own name and logo on their system.

The login screen a partner opens on Acme's system should say Acme, not
PartnerType; so should the sidebar and the e-mails the system sends for Acme.

The logo is a setting like the company name, kept in the settings table under
keys get_all() never returns (prefixed brand_), so the settings screen does not carry a
few hundred kilobytes of base64 on every load. Only PNG and JPEG are accepted,
checked by their first bytes rather than by what the browser claims: an SVG is
a document that can carry script, and this file is served to anybody who
opens the login page.
"""
import base64
import binascii
import hashlib
from datetime import datetime

from api import audit
from api.authz import can_configure
from api.db import get_conn
from api.settings_api import DEFAULTS, get_all
from api.i18n import t

LOGO_KEY, TYPE_KEY, VERSION_KEY = "brand_logo", "brand_logo_type", "brand_logo_version"
MAX_LOGO_BYTES = 200 * 1024
MAX_SIDE = 2000
_MAGIC = ((b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff", "jpeg"))


def _read(keys):
    conn = get_conn()
    try:
        marks = ",".join("?" * len(keys))
        rows = conn.execute(f"SELECT key, value FROM settings WHERE key IN ({marks})", tuple(keys)).fetchall()
        return {row["key"]: row["value"] for row in rows}
    finally:
        conn.close()


def logo():
    """(bytes, "png" | "jpeg") or None."""
    stored = _read((LOGO_KEY, TYPE_KEY))
    if not stored.get(LOGO_KEY) or stored.get(TYPE_KEY) not in ("png", "jpeg"):
        return None
    try:
        return base64.b64decode(stored[LOGO_KEY]), stored[TYPE_KEY]
    except (binascii.Error, ValueError):
        return None


def public_info():
    """What the login page may know: the name the producer chose, and whether there is a logo."""
    settings = get_all()
    name = " ".join(str(settings.get("company_name") or "").split())
    version = _read((VERSION_KEY,)).get(VERSION_KEY) if logo() else None
    return {
        # The old default name is nobody's brand: the page keeps PartnerType.
        "company_name": name if name and name != DEFAULTS["company_name"] else None,
        "logo_version": version or None,
        # The partner sign-up form links the programme's terms here (js/branding.js).
        "partner_terms_url": settings.get("partner_terms_url") or None,
    }


def _image_type(data):
    for magic, kind in _MAGIC:
        if data.startswith(magic):
            return kind
    return None


def _png_too_large(data):
    # IHDR: width and height are the 4-byte big-endian numbers at 16 and 20.
    if len(data) < 24:
        return True
    width, height = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    return not (0 < width <= MAX_SIDE and 0 < height <= MAX_SIDE)


def save_logo(raw, user):
    if not can_configure(user):
        raise PermissionError(t("Only an administrator can change the logo."))
    if not isinstance(raw, str) or not raw:
        raise ValueError(t("Choose a logo file."))
    if raw.startswith("data:") and "," in raw:
        raw = raw.split(",", 1)[1]
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError(t("Could not read the file. Choose a PNG or JPG image."))
    kind = _image_type(data)
    if kind is None:
        raise ValueError(t("The logo must be a PNG or JPG image."))
    if len(data) > MAX_LOGO_BYTES:
        raise ValueError(t("The logo can be at most 200 KB."))
    if kind == "png" and _png_too_large(data):
        raise ValueError(t("The logo can be at most {side} × {side} pixels.", side=MAX_SIDE))
    version = hashlib.sha256(data).hexdigest()[:12]
    now = datetime.utcnow().isoformat()
    conn = get_conn()
    try:
        for key, value in ((LOGO_KEY, base64.b64encode(data).decode()), (TYPE_KEY, kind), (VERSION_KEY, version)):
            conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, value, now))
        conn.commit()
    finally:
        conn.close()
    audit.log(user, "settings.update", "settings", LOGO_KEY, f"logo: {kind}, {len(data) // 1024} KB")
    return public_info()


def remove_logo(user):
    if not can_configure(user):
        raise PermissionError(t("Only an administrator can change the logo."))
    conn = get_conn()
    try:
        conn.execute("DELETE FROM settings WHERE key IN (?,?,?)", (LOGO_KEY, TYPE_KEY, VERSION_KEY))
        conn.commit()
    finally:
        conn.close()
    audit.log(user, "settings.update", "settings", LOGO_KEY, t("logo: removed"))
    return public_info()


# ─── HTTP ─────────────────────────────────────────────────────────────────────

def handle_public(handler):
    return 200, public_info()


def handle_logo_file(handler):
    """The image itself, or a JSON 404. Returns (None, None) once the bytes are written."""
    found = logo()
    if not found:
        return 404, {"error": t("No logo.")}
    data, kind = found
    handler.send_response(200)
    handler.send_header("Content-Type", f"image/{kind}")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Content-Security-Policy", "default-src 'none'")
    # The page asks with ?v=<version>, so a new logo is a new address.
    handler.send_header("Cache-Control", "public, max-age=86400")
    handler.end_headers()
    handler.wfile.write(data)
    return None, None


def handle_upload(handler, user, body):
    try:
        return 200, save_logo(body.get("data"), user)
    except PermissionError as error:
        return 403, {"error": str(error)}
    except ValueError as error:
        return 400, {"error": str(error)}


def handle_delete(handler, user):
    try:
        return 200, remove_logo(user)
    except PermissionError as error:
        return 403, {"error": str(error)}
