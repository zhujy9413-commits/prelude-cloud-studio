"""Production transport checks with temporary data and a fake video provider."""
import io
import fcntl
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch
from wsgiref.util import setup_testing_defaults
from wsgiref.validate import validator

from PIL import Image
import production
import backup_studio
from test_studio import FakeProvider


class ProductionHttpChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.provider = FakeProvider()
        self.studio = production.Studio(Path(self.temp.name), self.provider, invite_only=True)
        self.origin = 'https://studio.example.com'
        self.application = production.ProductionApp(self.studio, self.origin)

    def tearDown(self):
        self.application.close()
        self.temp.cleanup()

    def call(self, path='/api/bootstrap', method='GET', data=None, cookie=None, headers=None, **extra):
        env = {}
        setup_testing_defaults(env)
        env.update({'REQUEST_METHOD': method, 'PATH_INFO': path.split('?')[0], 'RAW_URI': path,
                    'QUERY_STRING': path.partition('?')[2], 'REMOTE_ADDR': '127.0.0.1',
                    'HTTP_HOST': 'studio.example.com', 'HTTP_X_FORWARDED_PROTO': 'https',
                    'HTTP_X_PRELUDE_CLIENT_IP': '192.0.2.1', 'wsgi.input': io.BytesIO()})
        if data is not None:
            body = data if isinstance(data, bytes) else json.dumps(data).encode()
            env.update({'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(body)), 'wsgi.input': io.BytesIO(body)})
        if cookie:
            env['HTTP_COOKIE'] = cookie
        for name, value in (headers or {}).items():
            env['HTTP_' + name.upper().replace('-', '_')] = value
        env.update(extra)
        captured = {}

        def respond(status, response_headers, exc_info=None):
            captured.update(status=int(status.split()[0]), headers=dict(response_headers))
            return lambda body: None

        result = validator(self.application)(env, respond)
        try:
            body = b''.join(result)
        finally:
            result.close()
        return captured['status'], captured['headers'], body

    def browser(self, cookie=None):
        status, headers, body = self.call(cookie=cookie)
        self.assertEqual(status, 200)
        return headers.get('Set-Cookie', '').split(';')[0] or cookie, json.loads(body)

    def post(self, browser, path, payload, **extra):
        return self.call(path, 'POST', payload, browser[0],
                         {'Origin': self.origin, 'X-Prelude-CSRF': browser[1]['csrf']}, **extra)

    def signed(self, name='小雨'):
        guest = self.browser()
        _, code = self.studio.access.invite(10)
        status, headers, _ = self.post(guest, '/api/access/activate',
            {'name': name, 'password': 'a memorable password phrase', 'invitation': code})
        self.assertEqual(status, 200)
        return self.browser(headers['Set-Cookie'].split(';')[0])

    def test_homepage_secure_cookie_and_private_configuration_boundaries(self):
        status, headers, body = self.call('/')
        self.assertEqual(status, 200)
        self.assertIn('序幕'.encode(), body)
        cookie = headers['Set-Cookie']
        self.assertTrue(cookie.startswith('__Host-prelude_session='))
        for flag in ('HttpOnly', 'Secure', 'SameSite=Strict', 'Path=/'):
            self.assertIn(flag, cookie)
        self.assertNotIn('Domain=', cookie)
        for path in ('/.env.local', '/.studio/studio.sqlite3', '/server.py', '/deploy/studio.env.example', '/../access.py'):
            self.assertEqual(self.call(path)[0], 404)

    def test_rejects_direct_access_wrong_host_and_untrusted_proxy_metadata(self):
        for extra in ({'REMOTE_ADDR': '192.0.2.4'}, {'HTTP_HOST': 'evil.example.com'},
                      {'HTTP_X_FORWARDED_PROTO': 'http'}, {'HTTP_X_FORWARDED_PROTO': 'https,http'},
                      {'HTTP_X_PRELUDE_CLIENT_IP': ''}, {'HTTP_X_PRELUDE_CLIENT_IP': '192.0.2.1, 192.0.2.2'}):
            with self.subTest(extra=extra):
                self.assertEqual(self.call(**extra)[0], 403)
        self.assertEqual(self.provider.submits, 0)

    def test_csrf_same_origin_and_invitation_checks_precede_generation(self):
        guest = self.browser()
        payload = {'prompt': '云海', 'duration': 5, 'aspect': '16:9', 'request_id': 'production-test-request-12345'}
        self.assertEqual(self.call('/api/generations', 'POST', payload, guest[0])[0], 403)
        for origin in ('https://evil.example.com', 'http://studio.example.com', 'null'):
            self.assertEqual(self.call('/api/generations', 'POST', payload, guest[0],
                                      {'Origin': origin, 'X-Prelude-CSRF': guest[1]['csrf']})[0], 403)
        self.assertEqual(self.post(guest, '/api/generations', payload)[0], 401)
        self.assertEqual(self.call('/api/bootstrap', headers={'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.assertEqual(self.call('/', headers={'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate',
                                                'Sec-Fetch-Dest': 'document'})[0], 200)
        self.assertEqual(self.provider.submits, 0)

    def test_proxy_client_ip_overrides_untrusted_forwarded_for_for_login_limits(self):
        guest = self.browser()
        with patch.object(self.studio.access, 'authenticate', return_value=('a' * 43, 'account-test')) as auth:
            self.assertEqual(self.post(guest, '/api/access/login', {},
                HTTP_X_FORWARDED_FOR='198.51.100.99', HTTP_X_REAL_IP='198.51.100.98',
                HTTP_X_PRELUDE_CLIENT_IP='2001:db8::1')[0], 200)
            self.assertEqual(auth.call_args.args[-1], '2001:db8::1')

    def test_upload_generation_quota_video_ranges_and_account_isolation(self):
        browser = self.signed()
        image = io.BytesIO()
        Image.new('RGB', (640, 360), 'navy').save(image, format='PNG')
        status, _, body = self.post(browser, '/api/references', image.getvalue(), CONTENT_TYPE='image/png')
        self.assertEqual(status, 201)
        reference = json.loads(body)
        payload = {'prompt': '云海缓慢翻卷', 'duration': 5, 'aspect': '16:9',
                   'request_id': 'production-test-request-12345', 'reference_id': reference['id']}
        status, _, body = self.post(browser, '/api/generations', payload)
        self.assertEqual(status, 202)
        job = json.loads(body)
        self.studio.process(job['id'])
        self.studio.process(job['id'])
        self.assertEqual(self.provider.submits, 1)
        self.assertEqual(self.post(browser, '/api/generations', payload)[0], 202)
        quota = self.browser(browser[0])[1]['access']['quota']
        self.assertEqual(quota['remaining_seconds'], 5)
        self.assertEqual(self.post(browser, '/api/generations', {**payload, 'duration': 6,
            'request_id': 'production-test-request-another'})[0], 429)
        video = '/api/generations/' + job['id'] + '/video'
        status, headers, body = self.call(video, cookie=browser[0], headers={'Range': 'bytes=4-11'})
        self.assertEqual(status, 206)
        self.assertEqual(body[:4], b'ftyp')
        self.assertEqual(len(body), 8)
        self.assertEqual(headers['Content-Range'], f'bytes 4-11/{len(FakeProvider.video())}')
        self.assertEqual(self.call(video, method='HEAD', cookie=browser[0])[2], b'')
        self.assertEqual(self.call(video, cookie=browser[0], headers={'Range': 'bytes=-8'})[2], FakeProvider.video()[-8:])
        self.assertEqual(self.call(video, cookie=browser[0], headers={'Range': 'bytes=99999999-'})[0], 416)
        stranger = self.signed('小风')
        for path in (video, reference['image_url'], '/api/generations/' + job['id']):
            self.assertEqual(self.call(path, cookie=stranger[0])[0], 404)
        self.assertEqual(self.post(browser, '/api/access/logout', {})[0], 200)
        self.assertEqual(self.call(video, cookie=browser[0])[0], 404)

    def test_large_upload_rejected_before_reading_and_health_does_not_issue_cookie(self):
        browser = self.signed()
        self.assertEqual(self.post(browser, '/api/references', b'not read', CONTENT_LENGTH=str(10 * 1024 * 1024 + 1))[0], 413)
        status, headers, body = self.call('/api/health')
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body), {'ok': False})
        self.assertNotIn('Set-Cookie', headers)
        self.assertEqual(self.provider.submits, 0)


class ProductionLifecycleChecks(unittest.TestCase):
    def test_factory_holds_lock_before_recovery_and_releases_on_close(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = {'PRELUDE_PUBLIC_ORIGIN': 'https://studio.example.com', 'PRELUDE_DATA_DIR': directory,
                        'PRELUDE_INVITE_ONLY': '1', 'PRELUDE_DAILY_LIMIT_CENTS': '3000',
                        'DASHSCOPE_API_KEY': 'offline-fixture-only',
                        'DASHSCOPE_API_HOST': 'ws-offline-fixture.cn-beijing.maas.aliyuncs.com'}
            with patch.dict(os.environ, settings), patch.object(production, 'Bailian', return_value=FakeProvider()):
                application = production.create_app()
                try:
                    self.assertTrue(application.worker.is_alive())
                    with self.assertRaises(RuntimeError):
                        production.create_app()
                    self.assertFalse(application.studio.stop.is_set())
                finally:
                    application.close()
                reopened = production.create_app()
                reopened.close()

    def test_invalid_public_configuration_fails_before_provider_or_disk_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'not-created'
            settings = {'PRELUDE_PUBLIC_ORIGIN': 'https://studio.example.com', 'PRELUDE_DATA_DIR': str(target),
                        'PRELUDE_INVITE_ONLY': '1', 'PRELUDE_DAILY_LIMIT_CENTS': '3000',
                        'DASHSCOPE_API_KEY': '', 'DASHSCOPE_API_HOST': ''}
            for changes in ({'PRELUDE_INVITE_ONLY': '0'}, {'PRELUDE_PUBLIC_ORIGIN': 'http://studio.example.com'},
                            {'PRELUDE_DATA_DIR': 'relative'}, {'PRELUDE_DAILY_LIMIT_CENTS': '0'}, {}):
                with self.subTest(changes=changes), patch.dict(os.environ, {**settings, **changes}), \
                        patch.object(production, 'Bailian') as provider, self.assertRaises(ValueError):
                    production.create_app()
                provider.assert_not_called()
                self.assertFalse(target.exists())

    def test_origin_rejects_ip_credentials_ports_paths_and_wildcards(self):
        for origin in ('', 'https://127.0.0.1', 'https://*.example.com', 'https://user@example.com',
                       'https://example.com/', 'https://example.com:443', 'https://EXAMPLE.com',
                       'https://example.com?x=1', 'https://localhost', 'https://example.com#fragment'):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                production.validate_origin(origin)

    def test_actual_gunicorn_process_serves_home_and_refuses_wrong_proxy_headers(self):
        import importlib.util
        if importlib.util.find_spec('gunicorn') is None:
            self.skipTest('Install requirements-production.txt for the Gunicorn integration check.')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'offline_entry.py').write_text(
                'from unittest.mock import patch\n'
                'from test_studio import FakeProvider\n'
                'import production\n'
                'def create():\n'
                '    with patch.object(production, "Bailian", return_value=FakeProvider()):\n'
                '        return production.create_app()\n')
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            project = Path(production.__file__).resolve().parent
            env = {**os.environ, 'PRELUDE_PUBLIC_ORIGIN': 'https://studio.example.com',
                   'PRELUDE_DATA_DIR': str(root / 'data'), 'PRELUDE_INVITE_ONLY': '1',
                   'PRELUDE_DAILY_LIMIT_CENTS': '3000', 'DASHSCOPE_API_KEY': 'offline-fixture-only',
                   'DASHSCOPE_API_HOST': 'ws-offline-fixture.cn-beijing.maas.aliyuncs.com',
                   'GUNICORN_CMD_ARGS': '',
                   'PYTHONPATH': os.pathsep.join([str(root), str(project / 'tests'), str(project)])}
            process = subprocess.Popen([sys.executable, '-m', 'gunicorn', '-c', str(project / 'deploy/gunicorn.conf.py'),
                                        '--bind', '127.0.0.1:' + str(port), 'offline_entry:create()'],
                                       cwd=project, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

            def request(path, headers):
                connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
                try:
                    connection.request('GET', path, headers=headers)
                    response = connection.getresponse()
                    return response.status, dict(response.getheaders()), response.read()
                finally:
                    connection.close()

            try:
                headers = {'Host': 'studio.example.com', 'X-Forwarded-Proto': 'https', 'X-Prelude-Client-IP': '192.0.2.1'}
                deadline = time.monotonic() + 10
                while True:
                    try:
                        status, _, body = request('/api/health', headers)
                        if status == 200:
                            break
                    except OSError:
                        pass
                    if process.poll() is not None or time.monotonic() >= deadline:
                        self.fail('Offline Gunicorn fixture did not become healthy.')
                    time.sleep(.05)
                self.assertEqual(json.loads(body), {'ok': True})
                status, result_headers, body = request('/', headers)
                self.assertEqual(status, 200)
                self.assertIn('序幕'.encode(), body)
                self.assertIn('Secure', result_headers['Set-Cookie'])
                self.assertEqual(request('/api/bootstrap', {'Host': 'studio.example.com'})[0], 403)
                self.assertEqual(request('/api/bootstrap', {**headers, 'Host': 'evil.example.com'})[0], 403)
            finally:
                process.terminate()
                try:
                    _, errors = process.communicate(timeout=12)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                    self.fail('Offline Gunicorn did not stop cleanly.')
            self.assertEqual(process.returncode, 0, errors.decode(errors='replace'))
            with patch.dict(os.environ, env), patch.object(production, 'Bailian', return_value=FakeProvider()):
                reopened = production.create_app()
                reopened.close()


class BackupChecks(unittest.TestCase):
    def test_backup_preserves_identity_reference_and_video_and_refuses_running_service(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            studio = production.Studio(root / 'data', FakeProvider(), invite_only=True)
            (studio.media_dir / 'fixture.mp4').write_bytes(b'private-video-fixture')
            (studio.reference_dir / 'fixture.jpg').write_bytes(b'private-frame-fixture')
            _, invitation = studio.access.invite(30)
            _, account_id = studio.access.authenticate('activate',
                {'name': '备份体验', 'password': 'a backup password phrase', 'invitation': invitation},
                'old-owner', 'old-token-hash', 'offline-peer')
            job = studio.create(account_id, {'prompt': '云海缓慢翻卷', 'duration': 5, 'aspect': '16:9',
                                            'request_id': 'backup-fixture-request-12345'})
            studio.process(job['id'])
            studio.process(job['id'])
            with (studio.data_dir / '.server.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaises(ValueError):
                    backup_studio.backup(studio.data_dir, root / 'backups')
            target = backup_studio.backup(studio.data_dir, root / 'backups')
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            with tarfile.open(target) as archive:
                names = archive.getnames()
                for name in ('studio.sqlite3', 'videos/fixture.mp4', 'references/fixture.jpg'):
                    self.assertIn('prelude-data/' + name, names)
                self.assertNotIn('prelude-data/.server.lock', names)
                archive.extractall(root / 'restore', filter='data')
            recovered = production.Studio(root / 'restore/prelude-data', FakeProvider(), invite_only=True)
            _, restored_id = recovered.access.authenticate('login',
                {'name': '备份体验', 'password': 'a backup password phrase'}, 'new-owner', 'new-token-hash', 'offline-peer')
            self.assertEqual(restored_id, account_id)
            self.assertEqual(recovered.access.quota(account_id)['remaining_seconds'], 25)
            self.assertEqual(recovered.get(job['id'], account_id)['status'], 'SUCCEEDED')
            self.assertEqual((recovered.media_dir / (job['id'] + '.mp4')).read_bytes(),
                             (studio.media_dir / (job['id'] + '.mp4')).read_bytes())
            with self.assertRaises(ValueError):
                backup_studio.backup(studio.data_dir, studio.data_dir / 'backups')


if __name__ == '__main__':
    unittest.main()
