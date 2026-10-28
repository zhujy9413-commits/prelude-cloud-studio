"""Offline provider tests and localhost HTTP checks. Never calls Bailian."""
from concurrent.futures import ThreadPoolExecutor
import http.client
import importlib.util
import json
import base64
import io
from functools import lru_cache
import subprocess
import sqlite3
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
from PIL import Image

spec = importlib.util.spec_from_file_location('studio_server', Path(__file__).resolve().parents[1] / 'server.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class FakeProvider:
    def __init__(self):
        self.submits = 0
        self.queries = 0
        self.submit_error = None
        self.query_error = None
        self.job = {'duration': 5, 'aspect': '16:9'}

    def submit(self, job):
        self.submits += 1
        self.job = job
        if self.submit_error:
            raise self.submit_error
        return {'output': {'task_id': 'task-123456789', 'task_status': 'PENDING'}}

    def query(self, _):
        self.queries += 1
        if self.query_error:
            raise self.query_error
        return {'output': {'task_status': 'SUCCEEDED', 'video_url': 'https://fake.oss-cn-beijing.aliyuncs.com/result.mp4'}}

    def download(self, _, target):
        target.write_bytes(self.video(self.job['duration'], self.job['aspect']))

    @staticmethod
    @lru_cache(maxsize=20)
    def video(duration=5, aspect='16:9'):
        size = app.SIZES.get(aspect, '1280*720').replace('*', 'x')
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'fixture.mp4'
            subprocess.run([app.encoder(), '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                f'color=c=navy:s={size}:r=12:d={duration}', '-c:v', 'libx264', '-preset', 'ultrafast',
                '-pix_fmt', 'yuv420p', '-threads', '2', '-movflags', '+faststart', str(target)], check=True, timeout=30)
            return target.read_bytes()


class StudioChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.provider = FakeProvider()
        self.studio = app.Studio(Path(self.temp.name) / 'data', self.provider)
        self.payload = {'request_id': 'request-1234567890', 'prompt': '暮色云海，云层缓慢翻卷', 'aspect': '16:9', 'duration': 5}

    def tearDown(self):
        self.temp.cleanup()

    def create(self, **changes):
        return self.studio.create('owner-a', {**self.payload, **changes})

    def test_same_request_is_idempotent_even_after_result(self):
        first = self.create()
        self.assertEqual(first['id'], self.create()['id'])
        self.studio.process(first['id'])
        self.studio.process(first['id'])
        self.assertEqual(self.create()['status'], 'SUCCEEDED')
        self.assertEqual(self.provider.submits, 1)
        self.assertEqual(self.provider.queries, 1)
        self.assertTrue((self.studio.media_dir / (first['id'] + '.mp4')).is_file())
        self.assertNotIn('task_id', self.create())

    def test_same_request_cannot_change_cost_or_prompt(self):
        self.create()
        with self.assertRaises(app.StudioError):
            self.create(duration=10)
        self.assertEqual(self.provider.submits, 0)

    def test_preparation_stages_and_observed_timing_are_persistent(self):
        clock = [100.0]
        download = self.provider.download
        def save_result(url, target):
            job = self.studio.get(first['id'])
            self.assertEqual(job['status'], 'DOWNLOADING')
            self.assertEqual(job['generation_completed_at'], 140)
            self.assertIsNone(job['ready_at'])
            clock[0] = 145
            download(url, target)
        def prepare(original, directory):
            job = self.studio.get(first['id'])
            self.assertEqual(job['status'], 'PREPARING')
            self.assertEqual(job['download_completed_at'], 145)
            self.assertIsNone(self.studio.public(job)['video_url'])
            with self.assertRaises(app.StudioError):
                self.create(request_id='preparing-second-request')
            clock[0] = 149
            return False  # Falling back to the original still becomes ready.
        self.provider.download = save_result
        with patch.object(app.time, 'time', side_effect=lambda: clock[0]), patch.object(app, 'prepare_media', side_effect=prepare):
            first = self.create()
            self.assertTrue(all(value is None for value in first['timings'].values()))
            clock[0] = 110
            self.studio.process(first['id'])
            clock[0] = 140
            self.studio.process(first['id'])
        public = self.create()
        self.assertEqual(public['status'], 'SUCCEEDED')
        self.assertEqual(public['timings'], dict(zip(app.TIMING_FIELDS, (110, 140, 145, 149))))
        self.studio.rename(first['id'], 'owner-a', '已完成')
        self.assertEqual(self.create()['timings'], public['timings'])
        self.assertEqual(self.provider.submits, 1)

    def test_preparing_recovers_saved_result_without_provider_calls(self):
        first = self.create()
        (self.studio.media_dir / (first['id'] + '.mp4')).write_bytes(FakeProvider.video())
        self.studio.update(first['id'], status='PREPARING', task_id='known-task-12345',
                           submitted_at=10, generation_completed_at=20, download_completed_at=21)
        recovered = app.Studio(self.studio.data_dir, provider=None)
        with patch.object(app, 'prepare_media', return_value=False):
            recovered.process(first['id'])
        job = recovered.get(first['id'])
        self.assertEqual(job['status'], 'SUCCEEDED')
        self.assertEqual(job['download_completed_at'], 21)
        self.assertIsNotNone(job['ready_at'])
        self.assertEqual((self.provider.submits, self.provider.queries), (0, 0))

    def test_download_retry_keeps_first_completion_observation(self):
        first = self.create()
        self.studio.process(first['id'])
        with patch.object(app.time, 'time', return_value=200), patch.object(self.provider, 'download', side_effect=app.ProviderError('retry later')):
            self.studio.process(first['id'])
        self.assertEqual(self.studio.get(first['id'])['generation_completed_at'], 200)
        self.studio.update(first['id'], next_check_at=0)
        with patch.object(app.time, 'time', return_value=210), patch.object(app, 'prepare_media', return_value=False):
            self.studio.process(first['id'])
        job = self.studio.get(first['id'])
        self.assertEqual(job['generation_completed_at'], 200)
        self.assertEqual(job['ready_at'], 210)
        self.assertEqual(self.provider.submits, 1)

    def test_concurrent_clicks_reserve_only_one_job(self):
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(lambda _: self.create(), range(5)))
        self.assertEqual(len({job['id'] for job in results}), 1)
        with self.assertRaises(app.StudioError):
            self.create(request_id='another-request-12345')

    def test_timeout_is_not_resubmitted_after_restart(self):
        first = self.create()
        self.provider.submit_error = app.ProviderError('connection interrupted')
        self.studio.process(first['id'])
        recovered = app.Studio(self.studio.data_dir, self.provider)
        recovered.process(first['id'])
        self.assertEqual(self.provider.submits, 1)
        self.assertEqual(recovered.get(first['id'])['status'], 'UNCERTAIN')
        with self.assertRaises(app.StudioError):
            recovered.create('owner-a', {**self.payload, 'request_id': 'different-request-12345'})

    def test_restart_resumes_get_only_for_known_task(self):
        first = self.create()
        self.studio.process(first['id'])
        recovered = app.Studio(self.studio.data_dir, self.provider)
        recovered.process(first['id'])
        self.assertEqual(recovered.get(first['id'])['status'], 'SUCCEEDED')
        self.assertEqual(self.provider.submits, 1)

    def test_restart_marks_unacknowledged_submission_uncertain(self):
        first = self.create()
        recovered = app.Studio(self.studio.data_dir, self.provider)
        self.assertEqual(recovered.get(first['id'])['status'], 'UNCERTAIN')
        recovered.process(first['id'])
        self.assertEqual(self.provider.submits, 0)

    def test_daily_limit_and_inputs_are_server_enforced(self):
        self.studio.daily_limit = 300
        with self.assertRaises(app.StudioError):
            self.create(duration=10)
        for changes in ({'duration': True}, {'duration': 16}, {'duration': 1}, {'duration': 3.5}, {'aspect': []}, {'prompt': ' '*10}, {'prompt': 'a'*1201}):
            with self.subTest(changes=changes), self.assertRaises(app.StudioError):
                self.create(**changes)
        self.assertEqual(self.provider.submits, 0)

    def test_custom_duration_preserves_seconds_and_cost(self):
        for seconds in (2, 7, 15):
            with self.subTest(seconds=seconds):
                job = self.create(duration=seconds, request_id=f'custom-duration-{seconds:03}')
                self.assertEqual(job['duration'], seconds)
                self.assertEqual(job['estimated_cost_cny'], f'{seconds * .6:.2f}')
                self.studio.process(job['id']); self.studio.process(job['id'])

    def test_other_session_cannot_read_a_job(self):
        first = self.create()
        with self.assertRaises(app.StudioError):
            self.studio.get(first['id'], 'owner-b')
        self.assertEqual(self.studio.list_jobs('owner-b'), [])

    def test_favorite_trash_restore_persist_without_regeneration(self):
        job = self.create()
        self.studio.process(job['id']); self.studio.process(job['id'])
        path = self.studio.media_dir / (job['id'] + '.mp4')
        original = path.read_bytes()
        self.assertTrue(self.studio.manage(job['id'], 'owner-a', 'favorite', {'favorite': True})['favorite'])
        recovered = app.Studio(self.studio.data_dir, self.provider)
        self.assertTrue(recovered.list_jobs('owner-a')[0]['favorite'])
        deleted = recovered.manage(job['id'], 'owner-a', 'trash', {})
        self.assertIsNotNone(deleted['deleted_at']); self.assertIsNone(deleted['video_url'])
        self.assertEqual(recovered.list_jobs('owner-a'), [])
        self.assertEqual(recovered.list_jobs('owner-a', deleted=True)[0]['id'], job['id'])
        self.assertEqual(recovered.manage(job['id'], 'owner-a', 'trash', {})['deleted_at'], deleted['deleted_at'])
        self.assertEqual(recovered.create('owner-a', self.payload)['id'], job['id'])
        restored = recovered.manage(job['id'], 'owner-a', 'restore', {})
        self.assertTrue(restored['favorite']); self.assertIsNone(restored['deleted_at'])
        self.assertIsNotNone(restored['video_url'])
        self.assertEqual(restored['estimated_cost_cny'], job['estimated_cost_cny'])
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.provider.submits, 1)

    def test_management_rejects_foreign_owners_and_invalid_favorites(self):
        job = self.create()
        for action, payload in (('favorite', {'favorite': True}), ('trash', {}), ('restore', {})):
            with self.subTest(action=action), self.assertRaises(app.StudioError) as error:
                self.studio.manage(job['id'], 'owner-b', action, payload)
            self.assertEqual(error.exception.status, 404)
        for value in (1, 'true', None, [], {}):
            with self.subTest(value=value), self.assertRaises(app.StudioError):
                self.studio.manage(job['id'], 'owner-a', 'favorite', {'favorite': value})
        for payload in ([], None, 'invalid'):
            with self.assertRaises(app.StudioError):
                self.studio.manage(job['id'], 'owner-a', 'restore', payload)
        self.assertFalse(self.studio.public(self.studio.get(job['id']))['favorite'])
        self.assertEqual(self.provider.submits, 0)

    def test_active_tasks_cannot_be_hidden_in_trash(self):
        job = self.create()
        for state in app.ACTIVE:
            self.studio.update(job['id'], status=state)
            with self.subTest(state=state), self.assertRaises(app.StudioError) as error:
                self.studio.manage(job['id'], 'owner-a', 'trash', {})
            self.assertEqual(error.exception.code, 'active_job')
            self.assertIsNone(self.studio.get(job['id'])['deleted_at'])

    def test_deleted_work_must_be_restored_before_editing(self):
        job = self.create(); self.studio.update(job['id'], status='FAILED')
        self.studio.manage(job['id'], 'owner-a', 'trash', {})
        with self.assertRaises(app.StudioError):
            self.studio.manage(job['id'], 'owner-a', 'favorite', {'favorite': True})
        with self.assertRaises(app.StudioError):
            self.studio.rename(job['id'], 'owner-a', 'new title')

    def test_legacy_database_migration_preserves_saved_work(self):
        folder = Path(self.temp.name) / 'legacy'; folder.mkdir()
        with sqlite3.connect(folder / 'studio.sqlite3') as db:
            db.execute('''CREATE TABLE jobs(id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_id TEXT NOT NULL,
                prompt TEXT NOT NULL, aspect TEXT NOT NULL, duration INTEGER NOT NULL, cost_cents INTEGER NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL, status TEXT NOT NULL, task_id TEXT,
                error TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '')''')
            db.execute("INSERT INTO jobs VALUES('legacy-id','owner-a','legacy-request','original prompt','16:9',7,420,1,1,'SUCCEEDED','task', '', 'original title')")
        migrated = app.Studio(folder, self.provider).list_jobs('owner-a')[0]
        self.assertEqual(migrated['title'], 'original title'); self.assertEqual(migrated['duration'], 7)
        self.assertFalse(migrated['favorite']); self.assertIsNone(migrated['deleted_at'])
        self.assertTrue(all(value is None for value in migrated['timings'].values()))

    def test_rename_is_persistent_owned_and_does_not_generate(self):
        first=self.create()
        renamed=self.studio.rename(first['id'],'owner-a','  暮色之上  ')
        self.assertEqual(renamed['title'],'暮色之上')
        self.assertEqual(self.studio.list_jobs('owner-a')[0]['title'],'暮色之上')
        with self.assertRaises(app.StudioError):
            self.studio.rename(first['id'],'owner-b','another title')
        for title in ('', 'x'*61, 'bad\nname', ['invalid']):
            with self.subTest(title=title), self.assertRaises(app.StudioError):
                self.studio.rename(first['id'],'owner-a',title)
        self.assertEqual(self.provider.submits,0)

    def test_lost_session_cannot_replay_an_existing_request(self):
        first = self.create()
        self.studio.process(first['id']); self.studio.process(first['id'])
        with self.assertRaises(app.StudioError) as context:
            self.studio.create('owner-b', self.payload)
        self.assertEqual(context.exception.code, 'request_session_changed')
        self.assertEqual(self.provider.submits, 1)

    def test_rejected_key_does_not_retry_or_block_future_explicit_job(self):
        first = self.create()
        self.provider.submit_error = app.ProviderError('authentication failed', rejected=True)
        self.studio.process(first['id'])
        self.studio.process(first['id'])
        self.assertEqual(self.provider.submits, 1)
        self.assertEqual(self.studio.get(first['id'])['status'], 'REJECTED')
        second = self.create(request_id='next-request-12345678')
        self.assertNotEqual(first['id'], second['id'])

    def test_query_network_error_keeps_task_without_post(self):
        first = self.create()
        self.studio.process(first['id'])
        self.provider.query_error = app.ProviderError('temporary error')
        self.studio.process(first['id'])
        self.provider.query_error = None
        self.studio.update(first['id'], next_check_at=0)
        self.studio.process(first['id'])
        self.assertEqual(self.provider.submits, 1)
        self.assertEqual(self.studio.get(first['id'])['status'], 'SUCCEEDED')

    def test_provider_payload_matches_t2v_contract(self):
        provider = object.__new__(app.Bailian)
        with patch.object(provider, 'request', return_value={}) as request:
            provider.submit({'prompt': 'new scene', 'aspect': '9:16', 'duration': 7})
        payload = request.call_args.args[1]
        self.assertEqual(payload['model'], 'wan2.6-t2v')
        self.assertEqual(payload['input'], {'prompt': 'new scene'})
        self.assertEqual(payload['parameters']['size'], '720*1280')
        self.assertEqual(payload['parameters']['duration'], 7)
        self.assertEqual(payload['parameters']['shot_type'], 'single')
        self.assertNotIn('img_url', payload['input'])

    def test_reference_submission_sends_the_actual_bundled_frame(self):
        provider = object.__new__(app.Bailian)
        with patch.object(provider, 'request', return_value={}) as request:
            provider.submit({'prompt':'人物缓慢向前行走，保持原景别', 'duration':8, 'reference_preset':'city'})
        payload=request.call_args.args[1]
        self.assertEqual(payload['model'],'wan2.6-i2v-flash')
        self.assertEqual(payload['parameters']['resolution'],'720P')
        self.assertEqual(payload['parameters']['duration'],8)
        self.assertTrue(payload['parameters']['audio'])
        self.assertEqual(payload['parameters']['shot_type'],'single')
        encoded=payload['input']['img_url'].split(',',1)[1]
        self.assertEqual(base64.b64decode(encoded),(app.ROOT/'dist/assets/city.webp').read_bytes())
        self.assertNotIn('size',payload['parameters'])

    def test_reference_mode_price_identity_and_restart(self):
        job=self.create(reference_preset='city', duration=8)
        self.assertEqual(job['model'],'wan2.6-i2v-flash')
        self.assertEqual(job['reference_image'],'/assets/city.webp')
        self.assertEqual(job['estimated_cost_cny'],'2.40')
        self.assertEqual(self.create(reference_preset='city',duration=8)['id'],job['id'])
        for reference in (None,'cloud'):
            with self.assertRaises(app.StudioError):
                self.create(reference_preset=reference,duration=8)
        recovered=app.Studio(self.studio.data_dir,self.provider)
        self.assertEqual(recovered.list_jobs('owner-a')[0]['reference_preset'],'city')
        self.assertEqual(self.provider.submits,0)

    def test_reference_input_cannot_supply_arbitrary_urls_paths_or_ratios(self):
        for value in ('../../.env.local','https://example.org/private.png',{},[],True,'unknown'):
            with self.subTest(value=value),self.assertRaises(app.StudioError):
                self.create(reference_preset=value)
        for aspect in ('9:16','1:1'):
            with self.assertRaises(app.StudioError):self.create(reference_preset='city',aspect=aspect)
        self.assertEqual(self.provider.submits,0)

    def test_download_rejects_unrelated_host_before_network(self):
        provider = object.__new__(app.Bailian)
        with patch.object(app.urllib.request, 'build_opener') as opener, self.assertRaises(app.ProviderError):
            provider.download('https://example.org/result.mp4', self.studio.media_dir / 'x.mp4')
        opener.assert_not_called()

    def image(self, size=(800, 450), format='PNG'):
        output=io.BytesIO(); Image.new('RGB',size,(48,72,110)).save(output,format=format)
        return output.getvalue()

    def test_upload_normalizes_deduplicates_and_persists_without_generating(self):
        content=self.image()
        first=self.studio.upload_reference('owner-a',content,'image/png','../海岸.png')
        second=self.studio.upload_reference('owner-a',content,'image/png','another.png')
        self.assertEqual(first['id'],second['id']);self.assertEqual(first['name'],'海岸.png')
        self.assertEqual(first['aspect'],'16:9')
        path=self.studio.reference_dir/(first['id']+'.jpg')
        with Image.open(path) as image:
            self.assertEqual(image.format,'JPEG');self.assertEqual(image.mode,'RGB');self.assertEqual(image.getexif(),{})
        recovered=app.Studio(self.studio.data_dir,self.provider)
        self.assertEqual(recovered.get_reference(first['id'],'owner-a'),first)
        with self.assertRaises(app.StudioError):recovered.get_reference(first['id'],'owner-b')
        self.assertEqual(self.provider.submits,0)

    def test_upload_rejects_spoofed_corrupt_oversized_and_animated_images(self):
        for content,mime in ((b'<svg onload="alert(1)"/>','image/png'),(self.image(),'image/jpeg'),(b'no image','image/webp')):
            with self.subTest(mime=mime),self.assertRaises(app.StudioError):self.studio.upload_reference('owner-a',content,mime,'x')
        for size in ((239,400),(9000,250),(1600,250)):
            with self.subTest(size=size),self.assertRaises(app.StudioError):self.studio.upload_reference('owner-a',self.image(size),'image/png','x')
        with patch.object(app,'MAX_UPLOAD_BYTES',10),self.assertRaises(app.StudioError):
            self.studio.upload_reference('owner-a',self.image(),'image/png','x')
        content=io.BytesIO();frames=[Image.new('RGB',(320,240),c) for c in ('red','blue')]
        frames[0].save(content,format='PNG',save_all=True,append_images=frames[1:])
        with self.assertRaises(app.StudioError):self.studio.upload_reference('owner-a',content.getvalue(),'image/png','animated.png')
        self.assertEqual(self.provider.submits,0)

    def test_upload_applies_orientation_and_removes_source_metadata(self):
        source=Image.new('RGB',(800,450),'blue');exif=Image.Exif();exif[274]=6;exif[270]='private camera metadata'
        output=io.BytesIO();source.save(output,format='JPEG',exif=exif)
        reference=self.studio.upload_reference('owner-a',output.getvalue(),'image/jpeg','rotated.jpg')
        self.assertEqual((reference['width'],reference['height']),(450,800))
        self.assertEqual(reference['aspect'],'9:16')
        with Image.open(self.studio.reference_dir/(reference['id']+'.jpg')) as normalized:
            self.assertEqual(dict(normalized.getexif()),{})
        self.assertEqual(self.provider.submits,0)

    def test_uploaded_reference_payload_cost_identity_and_original_ratio(self):
        reference=self.studio.upload_reference('owner-a',self.image((800,600)),'image/png','我的海岸.png')
        payload={**self.payload,'reference_id':reference['id'],'aspect':'4:3'}
        job=self.studio.create('owner-a',payload)
        self.assertEqual(job['model'],app.IMAGE_MODEL);self.assertEqual(job['estimated_cost_cny'],'1.50')
        self.assertEqual(job['reference_name'],'我的海岸.png');self.assertEqual(job['reference_image'],reference['image_url'])
        self.assertEqual(self.studio.create('owner-a',payload)['id'],job['id'])
        provider=object.__new__(app.Bailian)
        with patch.object(provider,'request',return_value={'output':{'task_id':'task-upload-123456'}}) as request:
            self.studio.provider=provider;self.studio.process(job['id'])
        sent=request.call_args.args[1]
        self.assertEqual(sent['model'],app.IMAGE_MODEL)
        data=sent['input']['img_url'];self.assertTrue(data.startswith('data:image/jpeg;base64,'))
        self.assertEqual(base64.b64decode(data.split(',',1)[1]),(self.studio.reference_dir/(reference['id']+'.jpg')).read_bytes())
        self.assertNotIn('_reference_bytes',self.studio.public(self.studio.get(job['id'])))
        other=self.studio.upload_reference('owner-a',self.image((900,600)),'image/png','other.png')
        with self.assertRaises(app.StudioError):self.studio.create('owner-a',{**payload,'reference_id':other['id'],'aspect':other['aspect']})

    def test_uploaded_references_cannot_cross_owners_or_supply_paths(self):
        reference=self.studio.upload_reference('owner-a',self.image(),'image/png','safe.png')
        for value in ('../../.env.local','https://example.org/image.png',{},[],True,'unknown'):
            with self.subTest(value=value),self.assertRaises(app.StudioError):self.create(reference_id=value)
        with self.assertRaises(app.StudioError):self.studio.create('owner-b',{**self.payload,'reference_id':reference['id']})
        with self.assertRaises(app.StudioError):self.create(reference_id=reference['id'],reference_preset='city')
        with self.assertRaises(app.StudioError):self.create(reference_id=reference['id'],aspect='9:16')
        self.assertEqual(self.provider.submits,0)


class HttpChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.provider = FakeProvider()
        cls.studio = app.Studio(Path(cls.temp.name) / 'http', cls.provider)
        cls.http = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        cls.http.studio = cls.studio
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown(); cls.http.server_close(); cls.thread.join(); cls.temp.cleanup()

    def call(self, path, method='GET', data=None, extra=None, cookie=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.http.server_port, timeout=5)
        headers = {'Host': '127.0.0.1:' + str(self.http.server_port)}
        if cookie:
            headers['Cookie'] = cookie
        if data is not None:
            headers['Content-Type'] = 'application/json'
        headers.update(extra or {})
        conn.request(method, path, json.dumps(data) if data is not None else None, headers)
        response = conn.getresponse(); body = response.read(); status = response.status; returned = dict(response.getheaders()); conn.close()
        return status, returned, body

    def test_bootstrap_cookie_and_no_secrets(self):
        status, headers, body = self.call('/api/bootstrap')
        self.assertEqual(status, 200)
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', headers['Set-Cookie'])
        self.assertTrue(json.loads(body)['ready'])
        self.assertNotIn(b'DASHSCOPE_API_KEY', body)

    def test_image_upload_http_requires_csrf_and_keeps_images_private(self):
        _,headers,body=self.call('/api/bootstrap');cookie=headers['Set-Cookie'].split(';')[0];csrf=json.loads(body)['csrf']
        output=io.BytesIO();Image.new('RGB',(640,360),'navy').save(output,format='PNG');content=output.getvalue()
        def upload(auth):
            conn=http.client.HTTPConnection('127.0.0.1',self.http.server_port,timeout=5)
            conn.request('POST','/api/references',content,headers={'Origin':f'http://127.0.0.1:{self.http.server_port}','Cookie':cookie,'Content-Type':'image/png',**auth})
            response=conn.getresponse();status=response.status;data=response.read();conn.close();return status,json.loads(data)
        calls=self.provider.submits
        self.assertEqual(upload({})[0],403)
        status,reference=upload({'X-Prelude-CSRF':csrf});self.assertEqual(status,201)
        self.assertEqual(self.call(reference['image_url'],cookie=cookie)[0],200)
        self.assertEqual(self.call(reference['image_url'])[0],404)
        self.assertEqual(self.call('/api/references/'+reference['id'])[0],404)
        self.assertEqual(self.call('/api/references/'+reference['id'],cookie=cookie)[0],200)
        self.assertEqual(self.provider.submits,calls)

    def test_cross_origin_and_missing_csrf_block_before_submit(self):
        previous_calls = self.provider.submits
        for headers in ({'Origin':'https://example.org'}, {'Origin':f'http://127.0.0.1:{self.http.server_port}'}):
            status, _, _ = self.call('/api/generations', 'POST', {}, headers)
            self.assertEqual(status, 403)
        self.assertEqual(self.provider.submits, previous_calls)

    def test_private_paths_and_dns_rebinding_are_blocked(self):
        for path in ('/.env.local','/../.env.local','/%2e%2e/server.py','/.studio/studio.sqlite3'):
            self.assertEqual(self.call(path)[0],404)
        self.assertEqual(self.call('/api/bootstrap',extra={'Host':'evil.example'})[0],403)

    def test_public_navigation_from_chat_keeps_api_private(self):
        headers={'Sec-Fetch-Site':'cross-site','Sec-Fetch-Mode':'navigate','Sec-Fetch-Dest':'document'}
        self.assertEqual(self.call('/workspace.html',extra=headers)[0],200)
        self.assertEqual(self.call('/api/bootstrap',extra=headers)[0],403)
        self.assertEqual(self.call('/.env.local',extra=headers)[0],404)

    def test_job_round_trip_session_isolation_range_and_download(self):
        _, headers, body = self.call('/api/bootstrap')
        bootstrap = json.loads(body); cookie = headers['Set-Cookie'].split(';')[0]
        post_headers={'Origin':f'http://127.0.0.1:{self.http.server_port}','X-Prelude-CSRF':bootstrap['csrf']}
        payload={'prompt':'one actual input','aspect':'1:1','duration':5,'request_id':'http-request-12345678'}
        status, _, body=self.call('/api/generations','POST',payload,post_headers,cookie)
        self.assertEqual(status,202); job=json.loads(body)
        self.studio.process(job['id']); self.studio.process(job['id'])
        self.assertEqual(self.call('/api/generations/'+job['id'])[0],404)
        _, _, body=self.call('/api/generations/'+job['id'],cookie=cookie)
        saved=json.loads(body); self.assertEqual(saved['status'],'SUCCEEDED')
        status, headers, body=self.call(saved['video_url'],cookie=cookie,extra={'Range':'bytes=0-7'})
        self.assertEqual(status,206); self.assertEqual(len(body),8); self.assertIn('Content-Range',headers)
        status, headers, body=self.call(saved['video_url']+'?download=1',cookie=cookie)
        self.assertEqual(status,200); self.assertIn('attachment',headers['Content-Disposition'])
        self.assertEqual(body[4:8],b'ftyp')
        status, _, body=self.call('/api/generations','POST',payload,post_headers,cookie)
        self.assertEqual(json.loads(body)['id'],job['id'])
        rename_path='/api/generations/'+job['id']+'/rename'
        self.assertEqual(self.call(rename_path,'POST',{'title':'暮色之上'},{'Origin':post_headers['Origin']},cookie)[0],403)
        status, _, body=self.call(rename_path,'POST',{'title':'暮色之上'},post_headers,cookie)
        self.assertEqual(status,200); self.assertEqual(json.loads(body)['title'],'暮色之上')
        _, other_headers, other_body=self.call('/api/bootstrap')
        other_cookie=other_headers['Set-Cookie'].split(';')[0]
        other_post={**post_headers,'X-Prelude-CSRF':json.loads(other_body)['csrf']}
        self.assertEqual(self.call(rename_path,'POST',{'title':'not yours'},other_post,other_cookie)[0],404)

    def test_management_http_auth_trash_visibility_and_recovery(self):
        _, headers, body = self.call('/api/bootstrap')
        cookie=headers['Set-Cookie'].split(';')[0]; bootstrap=json.loads(body)
        auth={'Origin':f'http://127.0.0.1:{self.http.server_port}', 'X-Prelude-CSRF':bootstrap['csrf']}
        payload={'prompt':'collection fixture','aspect':'16:9','duration':7,'request_id':'collection-http-request-12345'}
        _, _, body=self.call('/api/generations','POST',payload,auth,cookie); job=json.loads(body)
        self.studio.process(job['id']); self.studio.process(job['id'])
        calls=self.provider.submits; base='/api/generations/'+job['id']
        for action, data in (('favorite',{'favorite':True}),('trash',{}),('restore',{})):
            self.assertEqual(self.call(base+'/'+action,'POST',data,{'Origin':auth['Origin']},cookie)[0],403)
            self.assertEqual(self.call(base+'/'+action,'POST',data,{**auth,'Origin':'https://example.org'},cookie)[0],403)
        status, _, body=self.call(base+'/favorite','POST',{'favorite':True},auth,cookie)
        self.assertEqual(status,200); self.assertTrue(json.loads(body)['favorite'])
        self.assertEqual(self.call(base+'/trash','POST',{},auth,cookie)[0],200)
        _, _, body=self.call('/api/bootstrap',cookie=cookie); deleted=json.loads(body)
        self.assertNotIn(job['id'],[item['id'] for item in deleted['jobs']])
        self.assertIn(job['id'],[item['id'] for item in deleted['deleted_jobs']])
        self.assertEqual(self.call(base+'/video',cookie=cookie)[0],404)
        self.assertEqual(self.call(base+'/favorite','POST',{'favorite':False},auth,cookie)[0],409)
        _, foreign_headers, foreign_body=self.call('/api/bootstrap'); foreign_cookie=foreign_headers['Set-Cookie'].split(';')[0]
        foreign_auth={**auth,'X-Prelude-CSRF':json.loads(foreign_body)['csrf']}
        for action in ('favorite','trash','restore'):
            self.assertEqual(self.call(base+'/'+action,'POST',{'favorite':False},foreign_auth,foreign_cookie)[0],404)
        self.assertEqual(self.call(base+'/restore','POST',{},auth,cookie)[0],200)
        self.assertEqual(self.call(base+'/video',cookie=cookie)[0],200)
        self.assertEqual(self.provider.submits,calls)


if __name__ == '__main__':
    unittest.main()
