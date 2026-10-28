"""Temporary-share transport tests. All generation uses FakeProvider."""
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import share_runtime
import share_studio
import test_production as fixtures
from test_studio import FakeProvider


class SharingChecks(unittest.TestCase):
    browser = fixtures.ProductionHttpChecks.browser
    signed = fixtures.ProductionHttpChecks.signed
    post = fixtures.ProductionHttpChecks.post

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.provider = FakeProvider()
        self.studio = share_runtime.production.Studio(Path(self.temp.name), self.provider, invite_only=True)
        self.origin = 'https://gentle-clouds-preview.trycloudflare.com'
        self.application = share_runtime.SharingApp(self.studio, self.origin)

    def tearDown(self):
        self.application.close()
        self.temp.cleanup()

    def call(self, *args, **kwargs):
        return fixtures.ProductionHttpChecks.call(self, *args,
            **{'SERVER_PORT': str(self.application.tunnel_port),
               'HTTP_HOST': self.origin.removeprefix('https://'),
               'HTTP_CF_CONNECTING_IP': '192.0.2.1', **kwargs})

    def local_call(self, *args, **kwargs):
        return self.call(*args, **{'SERVER_PORT': str(self.application.local_port),
                                 'HTTP_HOST': '127.0.0.1:' + str(self.application.local_port), **kwargs})

    def test_public_host_spoof_cannot_switch_to_local_cookie_or_origin_rules(self):
        for extra in ({'HTTP_HOST': '127.0.0.1:4173'}, {'HTTP_HOST': 'localhost:4173'},
                      {'HTTP_HOST': 'previous-cloud.trycloudflare.com'}, {'REMOTE_ADDR': '192.0.2.2'},
                      {'HTTP_X_FORWARDED_PROTO': 'http'}, {'HTTP_CF_CONNECTING_IP': ''},
                      {'HTTP_CF_CONNECTING_IP': '192.0.2.1,192.0.2.2'}, {'SERVER_PORT': '9999'}):
            with self.subTest(extra=extra):
                self.assertEqual(self.call(**extra)[0], 403)
        status, headers, _ = self.call('/')
        self.assertEqual(status, 200)
        self.assertTrue(headers['Set-Cookie'].startswith('__Host-prelude_session='))
        self.assertIn('Secure', headers['Set-Cookie'])

    def test_local_account_and_works_survive_new_https_session_without_cookie_leak(self):
        public = self.signed()
        _, headers, body = self.local_call()
        local_cookie = headers['Set-Cookie'].split(';')[0]
        self.assertTrue(local_cookie.startswith('prelude_session='))
        csrf = json.loads(body)['csrf']
        status, headers, _ = self.local_call('/api/access/login', 'POST',
            {'name': '小雨', 'password': 'a memorable password phrase'}, cookie=local_cookie,
            headers={'Origin': 'http://127.0.0.1:4173', 'X-Prelude-CSRF': csrf})
        self.assertEqual(status, 200)
        signed_cookie = headers['Set-Cookie'].split(';')[0]
        local = json.loads(self.local_call(cookie=signed_cookie)[2])
        self.assertEqual(local['access']['account_id'], public[1]['access']['account_id'])
        self.assertEqual(local['access']['quota']['remaining_seconds'], 10)
        stranger = json.loads(self.call(cookie=signed_cookie)[2])
        self.assertFalse(stranger['access']['signed_in'])

    def test_public_csrf_invitation_and_personal_budget_remain_enforced(self):
        guest = self.browser()
        payload = {'prompt': '暮色云海', 'duration': 5, 'aspect': '16:9', 'request_id': 'sharing-fixture-123456789'}
        self.assertEqual(self.post(guest, '/api/generations', payload)[0], 401)
        signed = self.signed()
        self.assertEqual(self.call('/api/generations', 'POST', payload, signed[0],
            {'Origin': 'http://127.0.0.1:4173', 'X-Prelude-CSRF': signed[1]['csrf']})[0], 403)
        self.assertEqual(self.post(signed, '/api/generations', payload)[0], 202)
        self.assertEqual(self.post(signed, '/api/generations', payload)[0], 202)
        self.assertEqual(self.browser(signed[0])[1]['access']['quota']['remaining_seconds'], 5)
        self.assertEqual(self.provider.submits, 0)

    def test_origin_is_exact_and_only_accepts_assigned_cloudflare_hostname(self):
        for origin in ('https://example.com', 'http://gentle-clouds.trycloudflare.com',
                       'https://trycloudflare.com', 'https://cloud.trycloudflare.com.evil.example',
                       'https://*.trycloudflare.com', 'https://cloud.trycloudflare.com/'):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                share_runtime.validate_share_origin(origin)

    def test_listener_ports_block_host_spoofing_in_actual_gunicorn(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = Path(share_runtime.__file__).resolve().parent
            (root / 'offline_share.py').write_text(
                'from unittest.mock import patch\n'
                'from test_studio import FakeProvider\n'
                'import production,share_runtime\n'
                'def create():\n'
                '    with patch.object(production,"Bailian",return_value=FakeProvider()):\n'
                '        return share_runtime.create_app()\n')
            probes = [socket.socket(), socket.socket()]
            try:
                for probe in probes:
                    probe.bind(('127.0.0.1', 0))
                local_port, tunnel_port = [probe.getsockname()[1] for probe in probes]
            finally:
                for probe in probes:
                    probe.close()
            env = {**os.environ, 'PRELUDE_PUBLIC_ORIGIN': self.origin, 'PRELUDE_DATA_DIR': str(root / 'data'),
                   'PRELUDE_INVITE_ONLY': '1', 'PRELUDE_DAILY_LIMIT_CENTS': '3000',
                   'PRELUDE_LOCAL_PORT': str(local_port), 'PRELUDE_TUNNEL_PORT': str(tunnel_port),
                   'DASHSCOPE_API_KEY': 'offline-fixture-only',
                   'DASHSCOPE_API_HOST': 'ws-offline-fixture.cn-beijing.maas.aliyuncs.com',
                   'GUNICORN_CMD_ARGS': '', 'PYTHONPATH': os.pathsep.join([str(root),str(project / 'tests'),str(project)])}
            process = subprocess.Popen([sys.executable,'-m','gunicorn','-c',str(project / 'deploy/gunicorn-share.conf.py'),
                                        'offline_share:create()'],cwd=project,env=env,
                                       stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)

            def request(port, headers):
                connection = http.client.HTTPConnection('127.0.0.1',port,timeout=2)
                try:
                    connection.request('GET','/api/bootstrap',headers=headers)
                    response = connection.getresponse()
                    return response.status,dict(response.getheaders()),response.read()
                finally:
                    connection.close()

            try:
                deadline = time.monotonic() + 10
                while True:
                    try:
                        status, headers, _ = request(local_port, {'Host':f'127.0.0.1:{local_port}'})
                        if status == 200:
                            break
                    except OSError:
                        pass
                    if process.poll() is not None or time.monotonic() >= deadline:
                        self.fail('Offline sharing fixture did not start.')
                    time.sleep(.05)
                self.assertTrue(headers['Set-Cookie'].startswith('prelude_session='))
                public = {'Host': self.origin.removeprefix('https://'), 'X-Forwarded-Proto': 'https',
                          'CF-Connecting-IP': '192.0.2.1'}
                self.assertEqual(request(tunnel_port, public)[0], 200)
                for host in (f'127.0.0.1:{local_port}',f'localhost:{local_port}'):
                    self.assertEqual(request(tunnel_port,{**public,'Host':host,
                        'Server-Port':str(local_port),'X-Forwarded-Port':str(local_port)})[0],403)
            finally:
                process.terminate()
                try:
                    _, errors = process.communicate(timeout=12)
                except subprocess.TimeoutExpired:
                    process.kill(); process.communicate()
                    self.fail('Offline sharing fixture did not stop.')
            self.assertEqual(process.returncode,0,errors.decode(errors='replace'))


class SharingControlChecks(unittest.TestCase):
    def test_formal_start_requires_independent_network_validation_before_reading_private_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reports = ({}, {'real_bailian_calls':0,'production_data_changed':False,
                'public_https_homepage':True,'guest_generation_blocked':True,'activation_and_upload':True,
                'fake_generation_and_video_range':True,'account_isolation_and_logout':True,
                'checked_at':time.time()-90000})
            for report in reports:
                (root/'share-validation.json').write_text(json.dumps(report))
                with patch.object(share_studio,'RUNTIME',root),patch.object(share_studio,'config') as config, \
                        patch.object(share_studio.subprocess,'Popen') as process,self.assertRaises(ValueError):
                    share_studio.run_share()
                config.assert_not_called();process.assert_not_called()

    def test_connector_url_parser_only_accepts_exact_temporary_https_hostname(self):
        expected = 'https://gentle-clouds-preview.trycloudflare.com'
        self.assertEqual(share_studio.origin_from_line(json.dumps({'message': 'Visit ' + expected})), expected)
        for line in ('http://cloud.trycloudflare.com', 'https://cloud.trycloudflare.com.evil.example',
                     'https://localhost:4173', 'https://trycloudflare.com', 'https://cloud.trycloudflare.com:443'):
            self.assertIsNone(share_studio.origin_from_line(line))

    def test_stop_only_writes_marker_for_the_current_private_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertFalse(share_studio.request_stop(root))
            session = 'ab' * 16
            share_studio.save_state(root, {'status': 'running', 'session': session})
            self.assertEqual((root / 'share-session.json').stat().st_mode & 0o777,0o600)
            self.assertTrue(share_studio.request_stop(root))
            self.assertTrue((root / ('share-stop-' + session)).is_file())
            share_studio.save_state(root, {'status': 'closed', 'session': session})
            self.assertFalse(share_studio.request_stop(root))
            share_studio.save_state(root, {'status': 'running', 'session': '../../other'})
            self.assertFalse(share_studio.request_stop(root))


if __name__ == '__main__':
    unittest.main()
