"""Knowledge base: documents the producer shares with every partner.

Price lists, product sheets, presentations, contract templates. The producer's
staff (administrator, manager) add, rename and remove them; every signed-in
account, partners and observers included, can list and download them. The
database enforces the same split (migration 0019).

Two limits keep one producer from filling the server:

- one file: MAX_FILE_BYTES;
- all files together: the operator's quota, PARTNERTYPE_KNOWLEDGE_QUOTA_MB
  (tools/instance.py knowledge-quota). Without it DEFAULT_QUOTA_MB applies.
  A value that is not a whole number in range stops uploads instead of being
  guessed, the same way the gateway treats a bad limit.

Only file types we can recognise by their first bytes are accepted (PDF, Word,
Excel, PowerPoint, PNG, JPEG), never by what the browser claims. Downloads are
always sent as attachments with a fixed content type, so a file can never run
as a page of this application.
"""
import base64
import binascii
import contextvars
import hashlib
import io
import logging
import os
import re
import unicodedata
import zipfile
from datetime import datetime, timezone
from urllib.parse import quote

from psycopg import errors as database_errors

from api import audit
from api.authz import is_staff
from api.db import get_conn
from api.i18n import N_, t

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 10 * 1024 * 1024
DEFAULT_QUOTA_MB = 1024
MAX_QUOTA_MB = 1024 * 1024
QUOTA_ENV = "PARTNERTYPE_KNOWLEDGE_QUOTA_MB"
# The JSON body carries the file as base64 (4/3 of its size) plus the title.
MAX_UPLOAD_BODY_BYTES = 15 * 1024 * 1024
MAX_TITLE = 200
MAX_DESCRIPTION = 1000
MAX_FILENAME = 200
MAX_EXTENSION = 10
READ_ROLES = ("admin", "manager", "observer", "partner")
# A document added in the last few days carries a "new" badge.
NEW_DAYS = 7
# A download is read and sent in pieces of this size, so a file never sits in
# memory whole, however many partners fetch it at once.
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
BUSY_MESSAGE = N_("Other documents are being uploaded right now. Try again in a moment.")
_MEGABYTE = 1024 * 1024

CONTENT_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "png": "image/png",
    "jpeg": "image/jpeg",
}
EXTENSIONS = {
    "pdf": ("pdf",), "docx": ("docx",), "xlsx": ("xlsx",), "pptx": ("pptx",),
    "png": ("png",), "jpeg": ("jpg", "jpeg"),
}
# Office files are ZIP archives; the folder inside tells Word from Excel from
# PowerPoint. Only the list of names is read, nothing is extracted.
_OFFICE_FOLDERS = {"docx": "word/", "xlsx": "xl/", "pptx": "ppt/"}
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
# Invisible marks that change how the text around them is shown: direction
# marks, embeddings, overrides and isolates (which can make "fdp.exe" read as
# "exe.pdf"), the zero-width space and the byte order mark. The zero-width
# joiners stay, because emoji and some scripts need them.
_INVISIBLE = re.compile(r"[\u061c\u200b\u200e\u200f\u202a-\u202e\u2066-\u2069\ufeff]")
_QUOTA_VALUE = re.compile(r"[0-9]{1,7}")
# Sent with every download: nothing in the file may load, run or be framed.
DOWNLOAD_POLICY = "default-src 'none'; sandbox; frame-ancestors 'none'"


class QuotaError(ValueError):
    """The upload would go over the producer's space."""


class BusyError(RuntimeError):
    """Other uploads kept this one waiting too long; it may simply be retried."""


# ─── LIMITS ──────────────────────────────────────────────────────────────────

def quota_bytes():
    """The operator's limit in bytes, or None when the setting is invalid."""
    raw = os.environ.get(QUOTA_ENV, "").strip()
    if not raw:
        return DEFAULT_QUOTA_MB * _MEGABYTE
    # ASCII digits only: str.isdigit() also accepts characters int() refuses.
    if not _QUOTA_VALUE.fullmatch(raw) or not 1 <= int(raw) <= MAX_QUOTA_MB:
        logger.warning("knowledge_quota_invalid variable=%s", QUOTA_ENV)
        return None
    return int(raw) * _MEGABYTE


def used_bytes(conn):
    row = conn.execute("SELECT COALESCE(SUM(size_bytes), 0) AS used FROM knowledge_documents").fetchone()
    return int(row["used"])


def usage(conn=None):
    own = conn is None
    conn = conn or get_conn()
    try:
        return {"used_bytes": used_bytes(conn), "quota_bytes": quota_bytes(),
                "max_file_bytes": MAX_FILE_BYTES}
    finally:
        if own:
            conn.close()


# ─── VALIDATION ──────────────────────────────────────────────────────────────

def _can_read(user):
    return bool(user) and user.get("role") in READ_ROLES


def _collapse(value, multiline=False):
    """Text with control characters and runs of spaces folded, or None for a non-string."""
    if value is None:
        value = ""
    if not isinstance(value, str):
        return None
    value = _INVISIBLE.sub("", value)
    if multiline:
        lines = (" ".join(_CONTROL.sub(" ", line).split()) for line in value.splitlines())
        return "\n".join(lines).strip()
    return " ".join(_CONTROL.sub(" ", value).split())


def _title(value):
    title = _collapse(value)
    if title is None:
        raise ValueError(t("Invalid title."))
    if not title:
        raise ValueError(t("Enter a title."))
    if len(title) > MAX_TITLE:
        raise ValueError(t("The title can be at most {limit} characters.", limit=MAX_TITLE))
    return title


def _description(value):
    description = _collapse(value, multiline=True)
    if description is None:
        raise ValueError(t("Invalid description."))
    if len(description) > MAX_DESCRIPTION:
        raise ValueError(t("The description can be at most {limit} characters.", limit=MAX_DESCRIPTION))
    return description


def clean_filename(name):
    """A file name safe to store and to send back in a header."""
    if not isinstance(name, str):
        name = ""
    name = unicodedata.normalize("NFC", name).replace("\\", "/").rsplit("/", 1)[-1]
    # No leading dots or spaces, in any mix: the name must not turn into a
    # hidden file on the computer that saves it.
    name = _CONTROL.sub("", _INVISIBLE.sub("", name)).replace('"', "").strip().lstrip(". ")
    if len(name) > MAX_FILENAME:
        stem, dot, extension = name.rpartition(".")
        # Only a short tail after the last dot is an extension worth keeping;
        # a long one is part of the name and is cut with it.
        if dot and len(extension) <= MAX_EXTENSION:
            name = stem[:MAX_FILENAME - len(extension) - 1].rstrip() + dot + extension
        else:
            name = name[:MAX_FILENAME]
    return name


def detect_kind(data, filename):
    """The file type from its bytes (and, for Office files, the extension), or None."""
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"PK\x03\x04") and extension in _OFFICE_FOLDERS:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = archive.namelist()
        except (zipfile.BadZipFile, ValueError, OSError):
            return None
        folder = _OFFICE_FOLDERS[extension]
        if "[Content_Types].xml" in names and any(name.startswith(folder) for name in names):
            return extension
    return None


def _file_too_large():
    return t("The file can be at most {mb} MB.", mb=MAX_FILE_BYTES // _MEGABYTE)


def _decode(raw):
    if not isinstance(raw, str) or not raw:
        raise ValueError(t("Choose a file."))
    if raw.startswith("data:") and "," in raw:
        raw = raw.split(",", 1)[1]
    # Cheaper than decoding first: base64 is 4/3 of the file.
    if len(raw) > (MAX_FILE_BYTES * 4) // 3 + 4:
        raise ValueError(_file_too_large())
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError(t("Could not read the file."))


def _megabytes(size):
    return f"{size / _MEGABYTE:.1f} MB"


def _megabytes_text(size):
    """A size in megabytes for a message, with the reader's decimal separator."""
    whole, tenth = divmod(round(size * 10 / _MEGABYTE), 10)
    return t("{whole}.{tenth} MB", whole=whole, tenth=tenth)


# ─── OPERATIONS ──────────────────────────────────────────────────────────────

def _row_to_public(row, staff, now):
    created = row["created_at"]
    created_text = created.isoformat() if hasattr(created, "isoformat") else str(created)
    document = {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "filename": row["filename"],
        "kind": row["kind"],
        "size_bytes": row["size_bytes"],
        "created_at": created_text,
        "is_new": hasattr(created, "timestamp") and (now - created.timestamp()) < NEW_DAYS * 86400,
    }
    if staff:
        document["uploaded_by_name"] = row["uploaded_by_name"]
    return document


def list_documents(user):
    if not _can_read(user):
        raise PermissionError(t("You do not have access to the knowledge base."))
    staff = is_staff(user)
    # Only staff learn who uploaded a document, and only their query touches
    # the users table: a partner reads no staff rows (migration 0017).
    if staff:
        sql = """SELECT d.id, d.title, d.description, d.filename, d.kind, d.size_bytes,
                        d.created_at, u.name AS uploaded_by_name
                   FROM knowledge_documents d
                   LEFT JOIN users u ON u.id = d.uploaded_by
                  ORDER BY d.created_at DESC, d.id DESC"""
    else:
        sql = """SELECT id, title, description, filename, kind, size_bytes, created_at
                   FROM knowledge_documents
                  ORDER BY created_at DESC, id DESC"""
    conn = get_conn()
    try:
        rows = conn.execute(sql).fetchall()
        now = datetime.now(timezone.utc).timestamp()
        result = {"documents": [_row_to_public(row, staff, now) for row in rows]}
        if staff:
            result["usage"] = usage(conn)
        return result
    finally:
        conn.close()


def upload(body, user):
    if not is_staff(user):
        raise PermissionError(t("Only the producer adds documents."))
    title = _title(body.get("title"))
    description = _description(body.get("description"))
    filename = clean_filename(body.get("filename"))
    if not filename:
        raise ValueError(t("The file has no name."))
    data = _decode(body.get("data"))
    if not data:
        raise ValueError(t("The file is empty."))
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(_file_too_large())
    kind = detect_kind(data, filename)
    if kind is None:
        raise ValueError(t("Add a PDF, Word, Excel, PowerPoint, PNG or JPG file."))
    # A name without a dot has no extension at all: "pdf" is a stem, not ".pdf".
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in EXTENSIONS[kind]:
        suffix = f".{EXTENSIONS[kind][0]}"
        # Cut from the end of the stem, so the name keeps its beginning.
        filename = filename[:MAX_FILENAME - len(suffix)].rstrip() + suffix
    limit = quota_bytes()
    if limit is None:
        raise QuotaError(t("The space for documents is set incorrectly on the server. "
                           "Contact the operator."))

    conn = get_conn()
    try:
        conn.begin()
        # One upload at a time decides against the quota, so two parallel
        # uploads cannot both squeeze into the last free megabytes.
        try:
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('knowledge_documents', 0))")
        except database_errors.LockNotAvailable:
            # The database's lock timeout ran out behind slower uploads.
            raise BusyError(t(BUSY_MESSAGE)) from None
        used = used_bytes(conn)
        if used + len(data) > limit:
            raise QuotaError(t(
                "There is no room for this file: {used} of {quota} used. "
                "Delete old documents or move to a larger plan.",
                used=_megabytes_text(used), quota=_megabytes_text(limit)))
        cursor = conn.execute(
            """INSERT INTO knowledge_documents
                 (title, description, filename, kind, size_bytes, sha256, content, uploaded_by)
               VALUES (?,?,?,?,?,?,?,?)""",
            (title, description, filename, kind, len(data), hashlib.sha256(data).hexdigest(),
             data, user["id"]))
        document_id = cursor.lastrowid
        conn.commit()
    finally:
        conn.close()
    audit.log(user, "knowledge.upload", "knowledge_document", str(document_id),
              f"{title} ({filename}, {_megabytes(len(data))})")
    return {"id": document_id, "title": title, "filename": filename, "kind": kind,
            "size_bytes": len(data), "usage": usage()}


def update(document_id, body, user):
    if not is_staff(user):
        raise PermissionError(t("Only the producer changes documents."))
    changes = {}
    if "title" in body:
        changes["title"] = _title(body.get("title"))
    if "description" in body:
        changes["description"] = _description(body.get("description"))
    if not changes:
        raise ValueError(t("Nothing to change."))
    conn = get_conn()
    try:
        # Column names come from the fixed keys above, never from the request.
        assignments = ", ".join(f"{column} = ?" for column in changes)
        # One statement reads the old title and writes the change, so a
        # document deleted in between is reported as gone, not as renamed.
        before = conn.execute(
            f"""UPDATE knowledge_documents AS d SET {assignments}, updated_at = now()
                  FROM (SELECT id, title FROM knowledge_documents WHERE id = ?) AS old
                 WHERE d.id = old.id
             RETURNING old.title""",
            (*changes.values(), document_id)).fetchone()
        if before is None:
            raise LookupError(t("No such document."))
        conn.commit()
    finally:
        conn.close()
    audit.log(user, "knowledge.update", "knowledge_document", str(document_id),
              f"{before['title']} → {changes.get('title', before['title'])}")
    return {"id": document_id, **changes}


def delete(document_id, user):
    if not is_staff(user):
        raise PermissionError(t("Only the producer removes documents."))
    conn = get_conn()
    try:
        row = conn.execute("DELETE FROM knowledge_documents WHERE id = ? RETURNING title, filename",
                           (document_id,)).fetchone()
        if row is None:
            raise LookupError(t("No such document."))
        conn.commit()
        result = {"deleted": document_id, "usage": usage(conn)}
    finally:
        conn.close()
    audit.log(user, "knowledge.delete", "knowledge_document", str(document_id),
              f"{row['title']} ({row['filename']})")
    return result


def open_file(document_id, user):
    """(filename, kind, size, pieces) of one document; pieces yields its bytes.

    Only the size is read here. The bytes are read piece by piece while the
    response is sent, which happens after the routed handler has returned and
    left the request's database scope, so every piece is read in a copy of
    that scope: row-level security still sees the requester.
    """
    if not _can_read(user):
        raise PermissionError(t("You do not have access to the knowledge base."))
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT filename, kind, octet_length(content) AS size FROM knowledge_documents WHERE id = ?",
            (document_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise LookupError(t("No such document."))
    scope = contextvars.copy_context()
    return row["filename"], row["kind"], row["size"], _pieces(scope, document_id, row["size"])


def _read_piece(document_id, offset):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT substring(content FROM ? FOR ?) AS piece FROM knowledge_documents WHERE id = ?",
            (offset + 1, DOWNLOAD_CHUNK_BYTES, document_id)).fetchone()
    finally:
        conn.close()
    return bytes(row["piece"]) if row else b""


def _pieces(scope, document_id, size):
    offset = 0
    while offset < size:
        piece = scope.run(_read_piece, document_id, offset)
        # Deleted while it was being sent: the client gets a short download
        # rather than another document's bytes or an error page mid-file.
        if not piece:
            return
        yield piece
        offset += len(piece)


def content_disposition(filename):
    """attachment; with an ASCII fallback and the exact UTF-8 name (RFC 6266)."""
    ascii_name = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode()
    ascii_name = " ".join(re.sub(r'[^A-Za-z0-9._ -]', "_", ascii_name).split())
    # A name with no Latin letters at all still gets a readable fallback.
    if not ascii_name or ascii_name.startswith("."):
        ascii_name = "document" + ascii_name
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


# ─── HTTP ─────────────────────────────────────────────────────────────────────

def _parse_id(raw):
    if not isinstance(raw, str) or not raw.isascii() or not raw.isdigit() or len(raw) > 9:
        raise LookupError(t("No such document."))
    return int(raw)


def _errors(call):
    try:
        return 200, call()
    except PermissionError as error:
        return 403, {"error": str(error)}
    except LookupError as error:
        return 404, {"error": str(error)}
    except QuotaError as error:
        return 413, {"error": str(error), "code": "knowledge_quota"}
    except BusyError as error:
        return 503, {"error": str(error), "code": "knowledge_busy"}
    except ValueError as error:
        return 400, {"error": str(error)}


def handle_list(handler, user):
    return _errors(lambda: list_documents(user))


def handle_upload(handler, user, body):
    status, data = _errors(lambda: upload(body, user))
    return (201, data) if status == 200 else (status, data)


def handle_update(handler, user, raw_id, body):
    return _errors(lambda: update(_parse_id(raw_id), body, user))


def handle_delete(handler, user, raw_id):
    return _errors(lambda: delete(_parse_id(raw_id), user))


def handle_download(handler, user, raw_id):
    """The file itself, or a JSON error. Returns (None, None) once the response is set up.

    The body is handed to the transport as `handler.body_stream`, which sends
    it piece by piece.
    """
    try:
        filename, kind, size, pieces = open_file(_parse_id(raw_id), user)
    except PermissionError as error:
        return 403, {"error": str(error)}
    except LookupError as error:
        return 404, {"error": str(error)}
    handler.send_response(200)
    handler.send_header("Content-Type", CONTENT_TYPES[kind])
    handler.send_header("Content-Length", str(size))
    handler.send_header("Content-Disposition", content_disposition(filename))
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Content-Security-Policy", DOWNLOAD_POLICY)
    handler.end_headers()
    handler.body_stream = pieces
    return None, None
