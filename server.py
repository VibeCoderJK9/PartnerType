"""Flask HTTP transport. Domain routing preserves the existing API contract."""
import io
import logging
import os
import sys
import threading
import uuid
from pathlib import Path
from urllib.parse import unquote
from flask import Flask, Response, request, send_file
from werkzeug.exceptions import RequestEntityTooLarge, HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
from api.config import Config
from api import db
from api.i18n import language_from_header, locales_script, t, use_language
from api.router import UNEXPECTED_ERROR_MESSAGE, body_limit_for, dispatch
from api import affiliate, gateway
from api.knowledge import BUSY_MESSAGE, DOWNLOAD_POLICY
from api.http_security import build_cors_headers, MAX_JSON_BODY_BYTES

STATIC_DIR = Path(__file__).resolve().parent
# Large uploads buffered at the same time. Each holds a few copies of its body
# in memory until it is stored, and the app has one process with eight threads.
MAX_PARALLEL_LARGE_BODIES = 2
PUBLIC_ROOT_FILES = frozenset({
    "index.html", "register.html", "set-password.html", "manifest.json", "sw.js",
})
PUBLIC_ASSET_TYPES = {
    "pages": frozenset({".html"}),
    "js": frozenset({".js"}),
    "css": frozenset({".css", ".woff", ".woff2", ".ttf", ".otf"}),
    "img": frozenset({".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico"}),
}


def _is_public_path(parts):
    if not parts or any(not part or part.startswith((".", "__")) for part in parts):
        return False
    if len(parts) == 1:
        return parts[0] in PUBLIC_ROOT_FILES
    return Path(parts[-1]).suffix in PUBLIC_ASSET_TYPES.get(parts[0], ())


class HandlerAdapter:
    """The domain router's small request/response interface, backed by Flask.

    A handler writes its body to `wfile`, or sets `body_stream` to an iterable
    of bytes that is sent piece by piece, for a body too large to hold whole.
    """
    def __init__(self, config):
        self.headers = request.headers
        self.rfile = io.BytesIO(request.get_data())
        self.wfile = io.BytesIO()
        self.body_stream = None
        self.allowed_origins = config.origins
        self.status = None
        self.response_headers = []
        self.client_address = (request.remote_addr, 0)

    def send_response(self, code, message=None):
        self.status = code

    def send_header(self, name, value):
        self.response_headers.append((name, str(value)))

    def end_headers(self):
        pass


def _configure_logging():
    """One line per event on stdout, parseable without a log shipper.

    Deliberately not Flask's request logging: a URL here can carry an
    invitation or action token, which is why gunicorn's access log is off too.
    """
    root = logging.getLogger()
    if any(getattr(existing, '_partnertype', False) for existing in root.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        '{"time":"%(asctime)s","level":"%(levelname)s",'
        '"logger":"%(name)s","event":"%(message)s"}',
        datefmt='%Y-%m-%dT%H:%M:%S%z',
    ))
    handler._partnertype = True
    # Added, not substituted: replacing the root handlers would also silence
    # whatever the caller already attached, pytest's capture included.
    root.addHandler(handler)
    level = os.environ.get('PARTNERTYPE_LOG_LEVEL', 'INFO').upper()
    root.setLevel(getattr(logging, level, logging.INFO))


def create_app():
    config = Config.load()
    _configure_logging()
    app = Flask(__name__, static_folder=None)
    # The app is only ever reachable through this instance's own Caddy, so one
    # proxy hop is trusted -- two when the shared edge proxy sits in front of
    # it (tools/edge.py). Without it every client would share the proxy's
    # address, which would make the audit trail and any per-IP limit useless.
    hops = 2 if os.environ.get('PARTNERTYPE_PROXY_HOPS') == '2' else 1
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=1, x_host=0, x_prefix=0)
    app.config.update(MAX_CONTENT_LENGTH=MAX_JSON_BODY_BYTES, DEBUG=False)

    @app.after_request
    def security(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Permissions-Policy'] = (
            'camera=(), microphone=(), geolocation=(), payment=()')
        # Scripts must come from this origin and from a file. No 'unsafe-inline':
        # a policy that permits inline scripts permits equally any script an
        # attacker manages to write into a page. Every script lives in js/.
        #
        # Styles are split, because the two halves are not equally costly.
        # `style-src-elem` covers stylesheets and <style> blocks, and nothing
        # needs an inline one, so it is closed: an injected <style> can
        # read a page out one attribute selector at a time, which is a real way
        # to lift a token or a client list without running any script at all.
        # `style-src-attr` stays open for the 426 style="" attributes the pages
        # still carry -- a smaller exposure, since an attribute can only restyle
        # the element it sits on, and a separate piece of work. The plain
        # `style-src` behind them is the fallback for browsers that do not know
        # the two specific directives; it has to stay as permissive as the more
        # permissive half.
        #
        # What the rest buys: nothing may be loaded from another origin, the app
        # cannot be framed, forms cannot post elsewhere, and plugins and <base>
        # rewriting are refused outright.
        page_policy = '; '.join((
            "default-src 'self'",
            "script-src 'self'",
            # No third party is named here any more: the fonts are served from
            # this instance, so nothing on any page reaches outside it.
            "style-src 'self' 'unsafe-inline'",
            "style-src-elem 'self'",
            "style-src-attr 'unsafe-inline'",
            "img-src 'self' data:",
            "font-src 'self'",
            "connect-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ))
        # A downloaded document (api/knowledge.py) brings a stricter, sandboxed
        # policy of its own. Replacing it with the page policy would let a file
        # opened in the browser run scripts as part of this application. Only
        # that exact policy is kept; any other is replaced, so no response can
        # weaken the page policy by naming "sandbox" somewhere in its own.
        if response.headers.get('Content-Security-Policy') != DOWNLOAD_POLICY:
            response.headers['Content-Security-Policy'] = page_policy
        if request.path.startswith('/api/') or request.path == '/health':
            response.headers['Cache-Control'] = 'no-store'
            response.headers.pop('Access-Control-Allow-Origin', None)
            for key, value in build_cors_headers(request.headers.get('Origin'), config.origins).items():
                response.headers[key] = value
        # The gateway's other doors carry tokens, codes and CRM
        # records too, so no cache may keep them -- but they are not the app's
        # cookie API, so the credentialed-CORS headers above stay off them.
        if (request.path == '/mcp'
                or request.path.startswith(('/oauth/', '/.well-known/'))):
            response.headers['Cache-Control'] = 'no-store'
        if config.environment in ('staging', 'production'):
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response

    @app.errorhandler(RequestEntityTooLarge)
    def body_too_large(error):
        with use_language(language_from_header(request.headers.get("Accept-Language"))):
            return {"error": t("The request body is too large.")}, 413

    @app.errorhandler(Exception)
    def unexpected(error):
        if isinstance(error, HTTPException):
            return error
        request_id = uuid.uuid4().hex
        # Flask's default exception logger includes the raw URL, which can
        # contain an invitation/action token. Never delegate to that logger.
        app.logger.error('transport_error request_id=%s exception=%s method=%s',
                         request_id, type(error).__name__, request.method)
        with use_language(language_from_header(request.headers.get('Accept-Language'))):
            message = t(UNEXPECTED_ERROR_MESSAGE)
        return {'error': message, 'request_id': request_id}, 500

    @app.get('/health')
    def health():
        try:
            db.check_schema()
        except Exception:
            return {'status': 'unavailable'}, 503
        return {'status': 'ok'}

    # Before the legacy /api/<path:path> rule: /api/v1 is the bearer-only
    # gateway and must never fall through to the cookie router.
    gateway.register(app, config)
    affiliate.register(app, config)
    large_bodies = threading.BoundedSemaphore(MAX_PARALLEL_LARGE_BODIES)

    @app.route('/api/<path:path>', methods=['GET', 'POST', 'PATCH', 'PUT', 'DELETE', 'OPTIONS'])
    def api(path):
        if request.method == 'OPTIONS':
            return Response(status=204, headers={
                'Access-Control-Allow-Methods': 'GET, POST, PATCH, PUT, DELETE, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type'})
        # Set before the body is read: only a staff member's document upload
        # may exceed 1 MB. Anything larger from anyone else is refused from its
        # declared length, unread.
        limit = body_limit_for(request.method, request.path, request.headers, config.origins)
        request.max_content_length = limit
        if limit <= MAX_JSON_BODY_BYTES:
            return route()
        if not large_bodies.acquire(blocking=False):
            with use_language(language_from_header(request.headers.get('Accept-Language'))):
                return {'error': t(BUSY_MESSAGE), 'code': 'knowledge_busy'}, 503
        try:
            return route()
        finally:
            large_bodies.release()

    def route():
        handler = HandlerAdapter(config)
        dispatch(handler, request.method, request.full_path.rstrip('?'))
        if handler.status is None:
            return {"error": "Not found"}, 404
        body = handler.wfile.getvalue() if handler.body_stream is None else handler.body_stream
        return Response(body, status=handler.status, headers=handler.response_headers)

    @app.get('/locales.js')
    def locales():
        # Every locales/<code>.json in one script, loaded before js/app.js.
        response = Response(locales_script(), mimetype='text/javascript')
        response.headers['Cache-Control'] = 'no-cache'
        return response

    @app.get('/')
    @app.get('/<path:path>')
    def static(path='index.html'):
        parts = unquote(path).split('/')
        if not _is_public_path(parts) or '\\' in path or '\x00' in path:
            return Response(status=404)
        try:
            file = STATIC_DIR.joinpath(*parts).resolve(strict=True)
            if not file.is_file() or not _is_public_path(file.relative_to(STATIC_DIR).parts):
                return Response(status=404)
        except (ValueError, OSError, RuntimeError):
            return Response(status=404)
        response = send_file(file)
        response.headers['Cache-Control'] = 'no-cache'
        return response

    return app


def main():
    config = Config.load()
    if config.environment not in ('development', 'test'):
        raise RuntimeError('Use Gunicorn for staging/production')
    create_app().run(host='127.0.0.1', port=int(os.environ.get('PORT', '8000')), threaded=True, debug=False)


if __name__ == '__main__':
    main()
