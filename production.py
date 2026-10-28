"""Single-worker WSGI entry point behind the supplied local HTTPS proxy."""
from email.message import Message
import fcntl
from http import HTTPStatus
import io
import ipaddress
import os
from pathlib import Path
import re
import threading
from types import SimpleNamespace
import urllib.parse

from server import Bailian, JobError, ROOT, Studio, StudioError, StudioRequest


def validate_origin(value):
    parsed = urllib.parse.urlsplit(value)
    hostname = parsed.hostname or ''
    labels = hostname.split('.')
    if (value != 'https://' + hostname or len(hostname) > 253 or len(labels) < 2 or
            not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels)):
        raise ValueError('PRELUDE_PUBLIC_ORIGIN must be a lowercase HTTPS domain, without a path or port.')
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return value
    raise ValueError('PRELUDE_PUBLIC_ORIGIN must use a domain name, not an IP address.')


class ProxyRequest(StudioRequest):
    session_cookie = '__Host-prelude_session'
    secure_session = True

    def __init__(self, application, environ):
        self.application = application
        self.environ = environ
        self.server = SimpleNamespace(studio=application.studio)
        self.command = environ.get('REQUEST_METHOD', '')
        self.path = environ.get('RAW_URI') or urllib.parse.quote(environ.get('PATH_INFO', '/'), safe='/%')
        if 'RAW_URI' not in environ and environ.get('QUERY_STRING'):
            self.path += '?' + environ['QUERY_STRING']
        self.headers = Message()
        for key, value in environ.items():
            if key.startswith('HTTP_'):
                self.headers[key[5:].replace('_', '-')] = value
        for key in ('CONTENT_TYPE', 'CONTENT_LENGTH'):
            if key in environ:
                self.headers[key.replace('_', '-')] = environ[key]
        self.rfile = environ['wsgi.input']
        self.wfile = io.BytesIO()
        self.client_address = (environ.get('HTTP_X_PRELUDE_CLIENT_IP', ''), 0)
        self.status = 500
        self.response_headers = []
        self.file_body = None

    def expected_origin(self):
        # Only the local Caddy proxy may supply this header. X-Forwarded-For is ignored.
        if (self.environ.get('REMOTE_ADDR') != '127.0.0.1' or
                self.headers.get('X-Forwarded-Proto') != 'https'):
            raise StudioError('请通过网站的 HTTPS 入口访问', 403, 'proxy_required')
        try:
            ipaddress.ip_address(self.client_address[0])
        except ValueError:
            raise StudioError('无法确认访问来源', 403, 'proxy_required') from None
        origin = self.application.public_origin
        if self.headers.get('Host') != urllib.parse.urlsplit(origin).netloc:
            raise StudioError('无效的访问地址', 403, 'host_forbidden')
        return origin

    def send_response(self, status):
        self.status = status
        self.response_headers = []
        self.file_body = None
        self.wfile = io.BytesIO()

    def send_header(self, name, value):
        self.response_headers.append((name, value))

    def end_headers(self):
        pass

    def prepare_read(self):
        pass  # Caddy and Gunicorn enforce transport timeouts and body limits.

    def stream_file(self, path, start, length):
        self.file_body = (path, start, length)

    def dispatch(self):
        if urllib.parse.urlsplit(self.path).path == '/api/health' and self.command in ('GET', 'HEAD'):
            self.guard()
            ready = self.application.worker is not None and self.application.worker.is_alive()
            try:
                with self.server.studio.db() as db:
                    db.execute('SELECT 1').fetchone()
            except Exception:
                ready = False
            return self.json_response({'ok': ready}, 200 if ready else 503)
        return super().dispatch()

    def body(self):
        if self.file_body is None:
            yield self.wfile.getvalue()
            return
        path, start, remaining = self.file_body
        with path.open('rb') as handle:
            handle.seek(start)
            while remaining:
                block = handle.read(min(256 * 1024, remaining))
                if not block:
                    break
                remaining -= len(block)
                yield block


class ProductionApp:
    request_type = ProxyRequest

    def __init__(self, studio, public_origin, process_lock=None):
        if not studio.invite_only:
            raise ValueError('Public beta requires invitation access.')
        self.public_origin = validate_origin(public_origin)
        self.studio = studio
        self.process_lock = process_lock
        self.worker = None

    def start_worker(self):
        if self.worker is not None:
            raise RuntimeError('Generation worker already started.')
        self.worker = threading.Thread(target=self.studio.run_worker, name='prelude-generation', daemon=True)
        self.worker.start()

    def close(self):
        self.studio.stop.set()
        self.studio.wake.set()
        if self.worker is not None:
            self.worker.join(timeout=100)
            if self.worker.is_alive():
                return  # Keep the process lock until this process exits.
        if self.process_lock is not None:
            self.process_lock.close()
            self.process_lock = None

    def __call__(self, environ, start_response):
        request = self.request_type(self, environ)
        request.handle_request()
        start_response(f'{request.status} {HTTPStatus(request.status).phrase}', request.response_headers)
        return request.body()


def create_app(application_type=ProductionApp):
    # Gunicorn must load this factory after forking; never use --preload or >1 worker.
    origin = validate_origin(os.environ.get('PRELUDE_PUBLIC_ORIGIN', ''))
    data_dir = Path(os.environ.get('PRELUDE_DATA_DIR', ''))
    if not os.environ.get('PRELUDE_DATA_DIR') or not data_dir.is_absolute():
        raise ValueError('PRELUDE_DATA_DIR must be an absolute persistent directory.')
    if data_dir.resolve() == ROOT or ROOT / 'dist' == data_dir.resolve() or ROOT / 'dist' in data_dir.resolve().parents:
        raise ValueError('Private data must be outside the public directory.')
    if os.environ.get('PRELUDE_INVITE_ONLY', '1') != '1':
        raise ValueError('Public beta requires PRELUDE_INVITE_ONLY=1.')
    limit = int(os.environ.get('PRELUDE_DAILY_LIMIT_CENTS', '3000'))
    if not 300 <= limit <= 30000:
        raise ValueError('PRELUDE_DAILY_LIMIT_CENTS must be between 300 and 30000.')
    # Require explicit server credentials instead of falling back to a developer's local file.
    if not os.environ.get('DASHSCOPE_API_KEY') or not os.environ.get('DASHSCOPE_API_HOST'):
        raise ValueError('Configure the server-side Bailian environment before starting.')
    try:
        provider = Bailian()
    except JobError:
        raise ValueError('Bailian configuration is invalid; check the private server environment.') from None
    os.umask(0o077)
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    process_lock = (data_dir / '.server.lock').open('a')
    try:
        fcntl.flock(process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        process_lock.close()
        raise RuntimeError('This data directory already has a running Prelude worker.') from None
    try:
        studio = Studio(data_dir, provider, daily_limit_cents=limit, invite_only=True)
        application = application_type(studio, origin, process_lock)
        application.start_worker()
        return application
    except Exception:
        process_lock.close()
        raise
