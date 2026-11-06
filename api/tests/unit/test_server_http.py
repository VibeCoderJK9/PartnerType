"""Exercise the real HTTP boundary using only synthetic files and databases."""

import io

import pytest

import server
from api import db


@pytest.fixture
def http_app(tmp_path, monkeypatch):
    root = tmp_path / "portal"
    root.mkdir()
    public = {
        "index.html": b"<h1>Portal</h1>",
        "register.html": b"<h1>Register</h1>",
        "set-password.html": b"<h1>Password</h1>",
        "manifest.json": b'{}',
        "sw.js": b"// worker",
        "js/app.js": b"// public script",
        "css/main.css": b"body {}",
        "img/logo.svg": b"<svg></svg>",
        "pages/admin/settings.html": b"<h1>Settings</h1>",
    }
    private = [
        "database.db", "database.db-wal", "database.db-shm", ".env",
        ".git/config", ".planning/STATE.md", "backups/customer.db",
        "server.py", "requirements.txt", "node_modules/package/index.js",
        "js/__tests__/app.test.js", "js/private.db", "img/.secret.svg",
        "pages/admin/notes.txt",
    ]
    for path, data in list(public.items()) + [(p, b"PRIVATE-CANARY") for p in private]:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    sibling = tmp_path / "portal-private"
    sibling.mkdir()
    (sibling / "secret.js").write_bytes(b"PRIVATE-CANARY")
    (root / "js" / "outside.js").symlink_to(sibling / "secret.js")
    (root / "js" / "inside.js").symlink_to(root / "database.db")
    (root / "js" / "alias.js").symlink_to(root / "js" / "app.js")

    monkeypatch.setattr(server, "STATIC_DIR", root)
    client = server.create_app().test_client()
    def request(path):
        response = client.get(path)
        return response.status_code, dict(response.headers), response.data
    return request, public, None


def test_public_pages_and_assets_still_load(http_app):
    request, public, _ = http_app
    for path, contents in public.items():
        status, headers, body = request("/" + path + "?v=123")
        assert status == 200, path
        assert body == contents
        assert headers["X-Content-Type-Options"] == "nosniff"
    assert request("/")[2] == public["index.html"]


@pytest.mark.parametrize("path", [
    "/database.db", "/database.db-wal", "/database.db-shm", "/.env",
    "/.git/config", "/.planning/STATE.md", "/backups/customer.db",
    "/server.py", "/requirements.txt", "/node_modules/package/index.js",
    "/js/__tests__/app.test.js", "/js/private.db", "/img/.secret.svg",
    "/pages/admin/notes.txt", "/js/outside.js", "/js/inside.js",
    "/../portal-private/secret.js", "/js/../../portal-private/secret.js",
    "/js/../database.db", "/js/%2e%2e/database.db", "/%2eenv",
    "/js/%2f..%2fdatabase.db", "/js/app.js%00", "/js//app.js",
])
def test_private_files_and_traversal_are_not_served(http_app, path):
    status, _, body = http_app[0](path)
    assert status == 404
    assert b"PRIVATE-CANARY" not in body


def test_missing_files_return_404_instead_of_the_login_page(http_app):
    request = http_app[0]
    for path in ("/js/missing.js", "/pages/admin/missing.html", "/missing", "/css/"):
        assert request(path)[0] == 404


def test_symlink_to_a_public_asset_remains_available(http_app):
    assert http_app[0]("/js/alias.js")[2] == b"// public script"


def test_health_is_public_uncached_json(http_app):
    import json

    status, headers, body = http_app[0]("/health")
    assert status == 200
    assert json.loads(body) == {"status": "ok"}
    assert headers["Content-Type"].startswith("application/json")
    assert headers["Cache-Control"] == "no-store"
    assert int(headers["Content-Length"]) == len(body)


def test_health_reports_unavailable_without_disclosing_data(http_app, monkeypatch):
    def unavailable():
        raise RuntimeError('PRIVATE-CANARY')
    monkeypatch.setattr(db, 'check_schema', unavailable)
    status, headers, body = http_app[0]('/health')
    assert status == 503
    assert b'PRIVATE-CANARY' not in body
    assert headers['Cache-Control'] == 'no-store'


def test_unknown_api_is_json_404_not_empty_success(http_app):
    status, headers, body = http_app[0]('/api/does-not-exist')
    assert status == 404
    assert headers['Cache-Control'] == 'no-store'


def test_real_flask_login_cookie_csrf_and_no_store(admin_user):
    client = server.create_app().test_client()
    body = {'email': admin_user['email'], 'password': admin_user['plain_password'], 'role':'admin'}
    assert client.post('/api/auth/login',json=body).status_code == 403
    response = client.post('/api/auth/login',json=body,headers={'Origin':'http://localhost:8000'})
    assert response.status_code == 200
    assert 'HttpOnly' in response.headers['Set-Cookie']
    assert 'SameSite=Strict' in response.headers['Set-Cookie']
    assert response.headers['Cache-Control'] == 'no-store'
    assert client.get('/api/auth/me').status_code == 200
    assert client.patch('/api/settings',json={'company_name':'attack'},headers={'Origin':'https://evil.example'}).status_code == 403


def test_transport_error_never_logs_url_cookie_or_exception_text(monkeypatch, caplog):
    def fail(*args):
        raise RuntimeError('SECRET-EXCEPTION-CANARY')
    monkeypatch.setattr(server,'dispatch',fail)
    response=server.create_app().test_client().get('/api/invitations/SECRET-PATH-CANARY',headers={'Cookie':'secret=SECRET-COOKIE-CANARY'})
    assert response.status_code==500
    assert 'transport_error' in caplog.text
    assert 'SECRET-' not in caplog.text
    assert b'SECRET-' not in response.data


def test_every_response_carries_the_browser_side_defences(admin_user):
    """Headers the application cannot enforce on its own: framing, referrer
    leakage, and where scripts, styles and form posts may come from or go."""
    response = server.create_app().test_client().get('/index.html')

    policy = response.headers['Content-Security-Policy']
    assert "frame-ancestors 'none'" in policy
    assert "object-src 'none'" in policy
    assert "form-action 'self'" in policy
    assert "base-uri 'self'" in policy
    # A script must come from this origin AND from a file. Allowing inline
    # scripts would allow any inline script, including one written into a page
    # through a field somebody filled in -- which is most of what this header is
    # for. js/__tests__/no-inline-scripts.test.js keeps inline scripts out of
    # the pages.
    assert "script-src 'self'" in policy
    assert "script-src 'self' 'unsafe-inline'" not in policy
    # Styles are split. No page may carry or load a <style> of its own, which is
    # what an injected one would be; the pages' own style="" attributes are the
    # remaining, smaller exception. The plain style-src behind them is the
    # fallback for browsers that do not know the two specific directives.
    assert "style-src-elem 'self'" in policy
    assert "style-src-elem 'self' 'unsafe-inline'" not in policy
    assert "style-src-attr 'unsafe-inline'" in policy
    assert "style-src 'self' 'unsafe-inline'" in policy
    assert "connect-src 'self'" in policy
    # Fonts are served from this instance, so the policy names no outside host
    # at all -- a page that loads nothing foreign cannot leak who is reading it.
    assert "font-src 'self'" in policy
    assert "https://" not in policy
    assert response.headers['X-Frame-Options'] == 'DENY'
    assert response.headers['Referrer-Policy'] == 'no-referrer'
    assert 'camera=()' in response.headers['Permissions-Policy']


def test_the_api_carries_them_too_and_still_forbids_caching(admin_user):
    response = server.create_app().test_client().get('/api/auth/me')

    assert response.headers['Cache-Control'] == 'no-store'
    assert "frame-ancestors 'none'" in response.headers['Content-Security-Policy']


@pytest.mark.parametrize("hops,expected", [(None, "172.18.0.1"), ("2", "203.0.113.7")])
def test_the_client_address_survives_the_proxies_in_front(monkeypatch, hops, expected):
    # Alone, an instance has one proxy (its Caddy). Behind the shared edge
    # (tools/edge.py) it has two, and the client is the address the edge saw --
    # otherwise every visitor would share one address and one login limit.
    if hops:
        monkeypatch.setenv("PARTNERTYPE_PROXY_HOPS", hops)
    else:
        monkeypatch.delenv("PARTNERTYPE_PROXY_HOPS", raising=False)
    app = server.create_app()
    from flask import request as flask_request
    app.add_url_rule("/__probe", "probe", lambda: flask_request.remote_addr)

    response = app.test_client().get(
        "/__probe",
        headers={"X-Forwarded-For": "203.0.113.7, 172.18.0.1", "X-Forwarded-Proto": "https"},
        environ_base={"REMOTE_ADDR": "172.18.0.5"},
    )

    assert response.data.decode() == expected


def test_locales_script_serves_every_locale_file():
    import json as _json
    from pathlib import Path as _Path
    response = server.create_app().test_client().get('/locales.js')
    assert response.status_code == 200
    assert response.mimetype == 'text/javascript'
    body = response.get_data(as_text=True)
    assert body.startswith('window.PT_LOCALES = {')
    shipped = _json.loads(body[len('window.PT_LOCALES = '):].rstrip().rstrip(';'))
    on_disk = {path.stem for path in (_Path(server.__file__).parent / 'locales').glob('*.json')}
    assert set(shipped) == on_disk
    assert shipped['pl']['meta']['name'] == 'Polski'


def test_only_the_document_upload_may_send_more_than_one_megabyte(admin_user):
    import base64
    client = server.create_app().test_client()
    origin = {'Origin': 'http://localhost:8000'}
    body = {'email': admin_user['email'], 'password': admin_user['plain_password'], 'role': 'admin'}
    assert client.post('/api/auth/login', json=body, headers=origin).status_code == 200
    pdf = b'%PDF-1.4\n' + b'0' * (2 * 1024 * 1024)
    upload = {'title': 'Large price list', 'filename': 'price-list.pdf', 'data': base64.b64encode(pdf).decode()}
    response = client.post('/api/knowledge', json=upload, headers=origin)
    assert response.status_code == 201, response.get_json()
    response = client.get(f"/api/knowledge/{response.get_json()['id']}/file")
    # Sent in pieces as it is read, never held in memory whole.
    assert response.is_streamed
    assert response.status_code == 200 and response.data == pdf
    assert response.headers['Content-Length'] == str(len(pdf))
    assert response.headers['Content-Type'] == 'application/pdf'
    assert response.headers['Content-Disposition'].startswith('attachment;')
    assert response.headers['Cache-Control'] == 'no-store'
    # The file keeps its own, stricter policy instead of the page policy.
    assert 'sandbox' in response.headers['Content-Security-Policy']
    assert client.patch('/api/settings', json={'company_name': 'x' * (1024 * 1024 + 1)},
                        headers=origin).status_code == 413


def test_a_body_over_the_upload_limit_is_refused_before_it_is_read(admin_user):
    from api import knowledge
    # Even for staff, a body declared larger than the upload limit is refused
    # from its length alone: the stream fails the test if anything reads it.
    response = _unread_upload(_signed_in(admin_user), knowledge.MAX_UPLOAD_BODY_BYTES + 1)
    assert response.status_code == 413
    assert response.get_json()['error']


class _UnreadBody(io.RawIOBase):
    """A request body of `size` bytes that fails the test the moment anything reads it."""

    def __init__(self, size):
        super().__init__()
        self.size = size
        self.position = 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def seek(self, offset, whence=io.SEEK_SET):
        self.position = self.size + offset if whence == io.SEEK_END else offset
        return self.position

    def tell(self):
        return self.position

    def readinto(self, buffer):
        raise AssertionError('the request body was read')


def _signed_in(user):
    client = server.create_app().test_client()
    body = {'email': user['email'], 'password': user['plain_password'], 'role': user['role']}
    assert client.post('/api/auth/login', json=body,
                       headers={'Origin': 'http://localhost:8000'}).status_code == 200
    return client


def _unread_upload(client, size, origin='http://localhost:8000'):
    return client.post('/api/knowledge', input_stream=_UnreadBody(size),
                       content_type='application/json', headers={'Origin': origin})


def test_a_signed_out_upload_over_one_megabyte_is_refused_unread():
    # Without a session the larger limit is never granted, so the transport
    # refuses the body from its declared length instead of reading 15 MB first.
    response = _unread_upload(server.create_app().test_client(), 2 * 1024 * 1024)
    assert response.status_code == 413


def test_a_partner_upload_over_one_megabyte_is_refused_unread(partner_user):
    response = _unread_upload(_signed_in(partner_user), 2 * 1024 * 1024)
    assert response.status_code == 413


def test_a_cross_origin_upload_gets_no_larger_limit(admin_user):
    response = _unread_upload(_signed_in(admin_user), 2 * 1024 * 1024, origin='https://evil.example')
    assert response.status_code == 413


def test_only_a_few_large_uploads_are_buffered_at_once(admin_user, monkeypatch):
    import base64
    # No free place: the next large upload is turned away before its body is read.
    monkeypatch.setattr(server, 'MAX_PARALLEL_LARGE_BODIES', 0)
    client = _signed_in(admin_user)
    pdf = b'%PDF-1.4\n' + b'0' * (2 * 1024 * 1024)
    upload = {'title': 'Large price list', 'filename': 'price-list.pdf', 'data': base64.b64encode(pdf).decode()}
    response = client.post('/api/knowledge', json=upload, headers={'Origin': 'http://localhost:8000'})
    assert response.status_code == 503
    assert response.get_json()['code'] == 'knowledge_busy'
    assert response.get_json()['error'] == 'Other documents are being uploaded right now. Try again in a moment.'
    # Ordinary requests do not queue behind uploads.
    assert client.get('/api/knowledge').status_code == 200


@pytest.mark.parametrize('policy', [
    'sandbox allow-scripts allow-same-origin',
    "default-src *; report-uri https://example.com/sandbox",
])
def test_only_the_download_policy_replaces_the_page_policy(monkeypatch, policy):
    # Merely mentioning "sandbox" must not let a response escape the page policy.
    def respond(handler, method, path):
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/json')
        handler.send_header('Content-Security-Policy', policy)
        handler.wfile.write(b'{}')
    monkeypatch.setattr(server, 'dispatch', respond)
    response = server.create_app().test_client().get('/api/anything')
    assert response.headers['Content-Security-Policy'].startswith("default-src 'self'")
    assert "frame-ancestors 'none'" in response.headers['Content-Security-Policy']
