"""Local and temporary HTTPS access to one invitation-only studio and worker."""
import ipaddress
import os
import re
import urllib.parse

import production
from server import StudioError, StudioRequest


def validate_share_origin(value):
    production.validate_origin(value)
    if not re.fullmatch(r'https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com', value):
        raise ValueError('Temporary sharing must use the exact assigned trycloudflare.com HTTPS origin.')
    return value


class SharingRequest(production.ProxyRequest):
    def __init__(self, application, environ):
        super().__init__(application, environ)
        # Gunicorn's SERVER_PORT comes from the actual listening TCP socket, never Host.
        self.local = environ.get('SERVER_PORT') == str(application.local_port)
        self.session_cookie = 'prelude_session' if self.local else '__Host-prelude_session'
        self.secure_session = not self.local
        self.client_address = (environ.get('REMOTE_ADDR', '') if self.local else
                               environ.get('HTTP_CF_CONNECTING_IP', ''), 0)

    def expected_origin(self):
        if self.environ.get('REMOTE_ADDR') != '127.0.0.1':
            raise StudioError('请使用工作室入口访问', 403, 'proxy_required')
        if self.local:
            self.server.server_port = self.application.local_port
            return StudioRequest.expected_origin(self)
        if self.environ.get('SERVER_PORT') != str(self.application.tunnel_port):
            raise StudioError('无效的访问入口', 403, 'proxy_required')
        if (self.headers.get('X-Forwarded-Proto') != 'https' or
                self.headers.get('Host') != urllib.parse.urlsplit(self.application.public_origin).netloc):
            raise StudioError('请使用当前分享链接访问', 403, 'host_forbidden')
        try:
            ipaddress.ip_address(self.client_address[0])
        except ValueError:
            raise StudioError('无法确认访问来源', 403, 'proxy_required') from None
        return self.application.public_origin


class SharingApp(production.ProductionApp):
    request_type = SharingRequest

    def __init__(self, studio, public_origin, process_lock=None):
        validate_share_origin(public_origin)
        super().__init__(studio, public_origin, process_lock)
        self.local_port = int(os.environ.get('PRELUDE_LOCAL_PORT', '4173'))
        self.tunnel_port = int(os.environ.get('PRELUDE_TUNNEL_PORT', '4181'))
        if (not 1024 <= self.local_port <= 65535 or not 1024 <= self.tunnel_port <= 65535 or
                self.local_port == self.tunnel_port):
            raise ValueError('Local and tunnel ports must be distinct unprivileged ports.')


def create_app():
    validate_share_origin(os.environ.get('PRELUDE_PUBLIC_ORIGIN', ''))
    return production.create_app(application_type=SharingApp)
