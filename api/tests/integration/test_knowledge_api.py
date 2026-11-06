"""Router-level contract for the knowledge base (api/knowledge.py)."""
import base64
import io
import json
import threading
import zipfile

import pytest

from api import audit, knowledge
from api.postgres import no_context, user_context

PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


def _login(call_api, email, password, role):
    _, body = call_api("POST", "/api/auth/login", body={"email": email, "password": password, "role": role})
    return body["token"]


def _token(call_api, user):
    return _login(call_api, user["email"], user["plain_password"], user["role"])


def _office(folder, name="document.xml"):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(f"{folder}{name}", "<x/>")
    return buffer.getvalue()


def _upload(call_api, token, data=PDF, filename="price-list.pdf", title="Price list 2026", description=""):
    return call_api("POST", "/api/knowledge", token=token, body={
        "title": title, "description": description, "filename": filename,
        "data": base64.b64encode(data).decode()})


@pytest.fixture
def observer_token(call_api, admin_user):
    admin = _token(call_api, admin_user)
    status, body = call_api("POST", "/api/users", token=admin, body={
        "name": "Knowledge Observer", "email": "observer-kb@test.pl", "password": "ObserverPass123!",
        "company": "PartnerType", "role": "observer"})
    assert status == 201, body
    return _login(call_api, "observer-kb@test.pl", "ObserverPass123!", "observer")


def test_admin_adds_a_document_and_every_partner_sees_it(call_api, admin_user, partner_user, partner_user_bronze):
    admin = _token(call_api, admin_user)
    status, body = _upload(call_api, admin, description="Net prices\nfor partners")
    assert status == 201, body
    assert body["kind"] == "pdf" and body["size_bytes"] == len(PDF)
    assert body["usage"]["used_bytes"] == len(PDF)

    for partner in (partner_user, partner_user_bronze):
        status, listing = call_api("GET", "/api/knowledge", token=_token(call_api, partner))
        assert status == 200
        [document] = listing["documents"]
        assert document["title"] == "Price list 2026"
        assert document["description"] == "Net prices\nfor partners"
        assert document["is_new"] is True
        # Partners see neither who uploaded it nor the producer's space.
        assert "uploaded_by_name" not in document and "usage" not in listing

    status, listing = call_api("GET", "/api/knowledge", token=admin)
    assert listing["documents"][0]["uploaded_by_name"] == admin_user["name"]
    assert listing["usage"]["max_file_bytes"] == knowledge.MAX_FILE_BYTES


def test_partner_downloads_the_exact_bytes_as_an_attachment(call_api, call_api_raw, admin_user, partner_user):
    _, created = _upload(call_api, _token(call_api, admin_user), filename="Offer – winter.pdf")
    handler = call_api_raw("GET", f"/api/knowledge/{created['id']}/file", token=_token(call_api, partner_user))
    assert handler.send_response.call_args_list[0][0][0] == 200
    headers = dict(call.args for call in handler.send_header.call_args_list)
    assert headers["Content-Type"] == "application/pdf"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in headers["Content-Security-Policy"]
    assert headers["Content-Disposition"].startswith('attachment; filename="Offer winter.pdf"')
    assert "filename*=UTF-8''Offer%20%E2%80%93%20winter.pdf" in headers["Content-Disposition"]
    assert headers["Content-Length"] == str(len(PDF))
    # The bytes are read while the response is sent, after the router has left
    # the request's database scope; they must still be read as this partner.
    with no_context():
        assert b"".join(handler.body_stream) == PDF
    handler.wfile.write.assert_not_called()


def test_a_download_is_read_in_pieces_not_whole(call_api, call_api_raw, admin_user, partner_user, monkeypatch):
    monkeypatch.setattr(knowledge, "DOWNLOAD_CHUNK_BYTES", 16)
    _, created = _upload(call_api, _token(call_api, admin_user))
    handler = call_api_raw("GET", f"/api/knowledge/{created['id']}/file", token=_token(call_api, partner_user))
    pieces = list(handler.body_stream)
    assert len(pieces) == -(-len(PDF) // 16)
    assert all(len(piece) <= 16 for piece in pieces)
    assert b"".join(pieces) == PDF


def test_a_document_deleted_mid_download_ends_the_stream(call_api, call_api_raw, admin_user, partner_user,
                                                        monkeypatch):
    monkeypatch.setattr(knowledge, "DOWNLOAD_CHUNK_BYTES", 16)
    admin = _token(call_api, admin_user)
    _, created = _upload(call_api, admin)
    handler = call_api_raw("GET", f"/api/knowledge/{created['id']}/file", token=_token(call_api, partner_user))
    stream = iter(handler.body_stream)
    assert next(stream) == PDF[:16]
    assert call_api("DELETE", f"/api/knowledge/{created['id']}", token=admin)[0] == 200
    assert list(stream) == []


def test_partner_and_observer_cannot_change_anything(call_api, admin_user, partner_user, observer_token):
    _, created = _upload(call_api, _token(call_api, admin_user))
    partner = _token(call_api, partner_user)
    for token in (partner, observer_token):
        assert _upload(call_api, token)[0] == 403
        assert call_api("PATCH", f"/api/knowledge/{created['id']}", token=token, body={"title": "X"})[0] == 403
        assert call_api("DELETE", f"/api/knowledge/{created['id']}", token=token)[0] == 403
    status, listing = call_api("GET", "/api/knowledge", token=observer_token)
    assert status == 200 and len(listing["documents"]) == 1
    assert listing["documents"][0]["title"] == "Price list 2026"


def test_the_handlers_refuse_writes_even_past_the_route(admin_user, partner_user):
    # The route rule is the first line; the domain functions check again.
    with pytest.raises(PermissionError):
        knowledge.upload({"title": "T", "filename": "a.pdf", "data": base64.b64encode(PDF).decode()},
                         partner_user)
    with pytest.raises(PermissionError):
        knowledge.delete(1, partner_user)
    with pytest.raises(PermissionError):
        knowledge.list_documents({"id": 9, "role": "stranger"})


def test_manager_adds_edits_and_deletes(call_api, admin_user, manager_user):
    manager = _token(call_api, manager_user)
    status, created = _upload(call_api, manager)
    assert status == 201, created
    status, body = call_api("PATCH", f"/api/knowledge/{created['id']}", token=manager,
                            body={"title": "  Price   list 2027 ", "description": "New prices"})
    assert status == 200 and body["title"] == "Price list 2027"
    status, body = call_api("DELETE", f"/api/knowledge/{created['id']}", token=manager)
    assert status == 200 and body["usage"]["used_bytes"] == 0
    assert call_api("DELETE", f"/api/knowledge/{created['id']}", token=manager)[0] == 404
    assert call_api("GET", "/api/knowledge", token=manager)[1]["documents"] == []


def test_a_rename_racing_a_delete_reports_the_document_gone(call_api, admin_user, monkeypatch):
    admin = _token(call_api, admin_user)
    _, created = _upload(call_api, admin)
    real_get_conn = knowledge.get_conn

    class DeletedJustBefore:
        """Another staff member removes the document right before the rename is written."""

        def __init__(self, conn):
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._conn, name)

        def execute(self, sql, params=()):
            if "UPDATE knowledge_documents" in sql:
                other = real_get_conn()
                try:
                    other.execute("DELETE FROM knowledge_documents WHERE id = ?", (created["id"],))
                    other.commit()
                finally:
                    other.close()
            return self._conn.execute(sql, params)

    monkeypatch.setattr(knowledge, "get_conn", lambda: DeletedJustBefore(real_get_conn()))
    audit.log.reset_mock()
    status, body = call_api("PATCH", f"/api/knowledge/{created['id']}", token=admin, body={"title": "New"})
    assert status == 404, body
    assert not [call for call in audit.log.call_args_list if call.args[1] == "knowledge.update"]


def test_signed_out_requests_get_nothing(call_api, admin_user):
    _, created = _upload(call_api, _token(call_api, admin_user))
    assert call_api("GET", "/api/knowledge")[0] == 401
    assert call_api("GET", f"/api/knowledge/{created['id']}/file")[0] == 401


@pytest.mark.parametrize("data,filename,kind", [
    (PDF, "a.pdf", "pdf"),
    (b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, "logo.png", "png"),
    (b"\xff\xd8\xff\xe0" + b"\x00" * 32, "photo.jpg", "jpeg"),
    (_office("word/"), "contract.docx", "docx"),
    (_office("xl/", "workbook.xml"), "prices.xlsx", "xlsx"),
    (_office("ppt/", "presentation.xml"), "deck.pptx", "pptx"),
])
def test_accepted_file_types(call_api, admin_user, data, filename, kind):
    status, body = _upload(call_api, _token(call_api, admin_user), data=data, filename=filename)
    assert status == 201, body
    assert body["kind"] == kind


@pytest.mark.parametrize("data,filename", [
    (b"<svg onload=alert(1)></svg>", "picture.svg"),
    (b"<html><script>alert(1)</script></html>", "page.html"),
    (b"MZ\x90\x00", "program.exe"),
    (_office("word/"), "archive.zip"),
    (_office("xl/", "workbook.xml"), "pretends.docx"),
    (b"PK\x03\x04 not really a zip", "broken.docx"),
])
def test_refused_file_types(call_api, admin_user, data, filename):
    status, body = _upload(call_api, _token(call_api, admin_user), data=data, filename=filename)
    assert status == 400, body
    assert body["error"]


def test_a_pdf_with_a_misleading_name_is_stored_as_pdf(call_api, admin_user):
    status, body = _upload(call_api, _token(call_api, admin_user), filename="price-list.html")
    assert status == 201 and body["filename"] == "price-list.html.pdf"


@pytest.mark.parametrize("filename,stored", [
    (".pdf", "pdf.pdf"),
    ("pdf", "pdf.pdf"),
    ("price-list", "price-list.pdf"),
    ("price-list.PDF", "price-list.PDF"),
    # A name that is already as long as allowed loses the end of its stem to
    # the extension, never its beginning, and so never starts with a dot.
    ("Offer " + "x" * 194, "Offer " + "x" * 190 + ".pdf"),
    ("abcd." + "b" * 195, "abcd." + "b" * 191 + ".pdf"),
])
def test_the_stored_name_ends_in_the_real_extension(call_api, admin_user, filename, stored):
    status, body = _upload(call_api, _token(call_api, admin_user), filename=filename)
    assert status == 201, body
    assert body["filename"] == stored


def test_titles_and_descriptions_lose_direction_overrides(call_api, admin_user):
    status, body = _upload(call_api, _token(call_api, admin_user), title="Price\u202e list \u2067v2\u2069",
                           description="Net\u200b prices")
    assert status == 201, body
    assert body["title"] == "Price list v2"
    # Joiners stay: emoji and some scripts need them.
    status, body = _upload(call_api, _token(call_api, admin_user), title="\U0001f469\u200d\U0001f4bb guide")
    assert body["title"] == "\U0001f469\u200d\U0001f4bb guide"
    listing = call_api("GET", "/api/knowledge", token=_token(call_api, admin_user))[1]
    assert [doc["description"] for doc in listing["documents"]] == ["", "Net prices"]


def test_validation_messages(call_api, admin_user):
    admin = _token(call_api, admin_user)
    assert _upload(call_api, admin, title="   ")[0] == 400
    assert _upload(call_api, admin, title="x" * 201)[0] == 400
    assert _upload(call_api, admin, description="x" * 1001)[0] == 400
    assert _upload(call_api, admin, filename="")[0] == 400
    assert _upload(call_api, admin, data=b"")[0] == 400
    status, _ = call_api("POST", "/api/knowledge", token=admin,
                         body={"title": "T", "filename": "a.pdf", "data": "not-base64!"})
    assert status == 400
    status, _ = call_api("POST", "/api/knowledge", token=admin,
                         body={"title": ["T"], "filename": "a.pdf", "data": base64.b64encode(PDF).decode()})
    assert status == 400
    _, created = _upload(call_api, admin)
    assert call_api("PATCH", f"/api/knowledge/{created['id']}", token=admin, body={})[0] == 400


def test_errors_are_in_the_callers_language(call_api, admin_user):
    admin = _token(call_api, admin_user)
    status, body = call_api("POST", "/api/knowledge", token=admin, headers={"Accept-Language": "en"},
                            body={"title": "T", "filename": "a.gif", "data": base64.b64encode(b"GIF89a").decode()})
    assert status == 400 and body["error"] == "Add a PDF, Word, Excel, PowerPoint, PNG or JPG file."
    status, polish = _upload(call_api, admin, data=b"GIF89a", filename="a.gif")
    assert status == 400 and polish["error"] != body["error"]


def test_one_file_over_the_limit_is_refused(call_api, admin_user, monkeypatch):
    monkeypatch.setattr(knowledge, "MAX_FILE_BYTES", 1024)
    status, body = _upload(call_api, _token(call_api, admin_user), data=PDF + b"0" * 2048)
    assert status == 400 and "MB" in body["error"]


def test_the_quota_counts_every_document(call_api, admin_user, monkeypatch):
    admin = _token(call_api, admin_user)
    monkeypatch.setenv(knowledge.QUOTA_ENV, "1")
    big = PDF + b"0" * (700 * 1024)
    assert _upload(call_api, admin, data=big)[0] == 201
    status, body = _upload(call_api, admin, data=big, title="Second")
    assert status == 413 and body["code"] == "knowledge_quota"
    # Sizes are written the way the reader's language writes decimals.
    assert "0,7 MB" in body["error"] and "1,0 MB" in body["error"]
    status, english = call_api("POST", "/api/knowledge", token=admin, headers={"Accept-Language": "en"}, body={
        "title": "Third", "filename": "a.pdf", "data": base64.b64encode(big).decode()})
    assert status == 413 and "0.7 MB of 1.0 MB" in english["error"]
    assert len(call_api("GET", "/api/knowledge", token=admin)[1]["documents"]) == 1


def test_two_parallel_uploads_cannot_both_take_the_last_free_space(admin_user, monkeypatch):
    monkeypatch.setenv(knowledge.QUOTA_ENV, "1")
    big = PDF + b"0" * (700 * 1024)
    body = {"title": "Price list", "filename": "price-list.pdf", "data": base64.b64encode(big).decode()}
    # Each upload waits here, once it has read the space used, for the other
    # to read it too. Unless one decision waits for the other to be stored,
    # both read "nothing used yet" and both files are kept.
    barrier = threading.Barrier(2)
    real_used_bytes = knowledge.used_bytes
    reads = []

    def used_then_wait(conn):
        used = real_used_bytes(conn)
        reads.append(used)
        if len(reads) <= 2:
            try:
                barrier.wait(timeout=2)
            except threading.BrokenBarrierError:
                pass
        return used

    monkeypatch.setattr(knowledge, "used_bytes", used_then_wait)
    statuses = []

    def upload():
        # Each thread is its own request, signed in as the administrator.
        with user_context(admin_user):
            statuses.append(knowledge.handle_upload(None, admin_user, body)[0])

    threads = [threading.Thread(target=upload) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(statuses) == [201, 413]
    conn = knowledge.get_conn()
    try:
        assert conn.execute("SELECT count(*) AS n FROM knowledge_documents").fetchone()["n"] == 1
    finally:
        conn.close()


def test_an_upload_that_cannot_get_its_turn_is_told_to_try_again(call_api, admin_user, monkeypatch):
    from api.i18n import t
    admin = _token(call_api, admin_user)
    # Another upload holds the turn and keeps it longer than this one waits.
    holder = knowledge.get_conn()
    holder.begin()
    holder.execute("SELECT pg_advisory_xact_lock(hashtextextended('knowledge_documents', 0))")
    real_get_conn = knowledge.get_conn

    class ShortWait:
        def __init__(self, conn):
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._conn, name)

        def execute(self, sql, params=()):
            if "pg_advisory_xact_lock" in sql:
                self._conn.execute("SET LOCAL lock_timeout = '200ms'")
            return self._conn.execute(sql, params)

    monkeypatch.setattr(knowledge, "get_conn", lambda: ShortWait(real_get_conn()))
    try:
        status, body = _upload(call_api, admin)
    finally:
        holder.close()
    assert status == 503, body
    assert body["code"] == "knowledge_busy"
    assert body["error"] == t(knowledge.BUSY_MESSAGE, lang="pl")


@pytest.mark.parametrize("value", ["0", "abc", "-5", "99999999"])
def test_a_broken_quota_setting_stops_uploads(call_api, admin_user, monkeypatch, value):
    monkeypatch.setenv(knowledge.QUOTA_ENV, value)
    admin = _token(call_api, admin_user)
    status, body = _upload(call_api, admin)
    assert status == 413 and body["code"] == "knowledge_quota"
    assert call_api("GET", "/api/knowledge", token=admin)[1]["usage"]["quota_bytes"] is None


def test_upload_body_may_exceed_the_default_json_limit(call_api, admin_user):
    data = PDF + b"0" * (2 * 1024 * 1024)
    status, body = _upload(call_api, _token(call_api, admin_user), data=data)
    assert status == 201, body
    # Every other route keeps the 1 MB limit.
    status, _ = call_api("PATCH", "/api/knowledge/1", token=_token(call_api, admin_user),
                         raw_body=json.dumps({"title": "x" * (1024 * 1024 + 10)}).encode())
    assert status == 413


def test_actions_land_in_the_audit_log(call_api, admin_user):
    # The shared fixtures replace api.audit.log with a mock for every test.
    admin = _token(call_api, admin_user)
    audit.log.reset_mock()
    _, created = _upload(call_api, admin)
    call_api("PATCH", f"/api/knowledge/{created['id']}", token=admin, body={"title": "New"})
    call_api("DELETE", f"/api/knowledge/{created['id']}", token=admin)
    entries = [call.args for call in audit.log.call_args_list if call.args[1].startswith("knowledge.")]
    assert [entry[1] for entry in entries] == ["knowledge.upload", "knowledge.update", "knowledge.delete"]
    assert all(entry[2] == "knowledge_document" and entry[3] == str(created["id"]) for entry in entries)
    # The file itself never reaches the log, only its name and size.
    assert "price-list.pdf" in entries[0][4] and "%PDF" not in entries[0][4]


def test_bad_ids_are_not_found(call_api, admin_user):
    admin = _token(call_api, admin_user)
    assert call_api("GET", "/api/knowledge/999/file", token=admin)[0] == 404
    assert call_api("PATCH", "/api/knowledge/999", token=admin, body={"title": "x"})[0] == 404
    assert call_api("DELETE", "/api/knowledge/9999999999", token=admin)[0] == 404
