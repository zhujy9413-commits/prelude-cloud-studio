"""Fault injection only. All provider activity is a local fake."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
import struct
import time
import unittest
from unittest.mock import patch
from pathlib import Path

import media
import task_recovery
import test_studio as fixtures
import test_production as http_fixtures
FakeProvider, app = fixtures.FakeProvider, fixtures.app


class RecoveryChecks(unittest.TestCase):
    setUp, tearDown, create = fixtures.StudioChecks.setUp, fixtures.StudioChecks.tearDown, fixtures.StudioChecks.create
    def test_corrupt_download_is_never_published_and_get_only_recovers(self):
        first = self.create()
        self.studio.process(first['id'])
        with patch.object(self.provider, 'download', side_effect=lambda _, target: target.write_bytes(b'\0\0\0\x20ftyp'+bytes(120))):
            self.studio.process(first['id'])
        job = self.studio.get(first['id'])
        self.assertEqual(job['status'], 'UNCERTAIN')
        self.assertIsNone(self.studio.public(job)['video_url'])
        self.assertIsNone(job['ready_at'])
        self.assertTrue((self.studio.data_dir / 'quarantine' / (first['id']+'.mp4')).is_file())
        self.studio.update(first['id'], next_check_at=0)
        self.studio.process(first['id'])
        self.assertEqual(self.studio.get(first['id'])['status'], 'SUCCEEDED')
        self.assertEqual(self.provider.submits, 1)
        self.assertEqual(self.provider.queries, 2)

    def test_unknown_retries_are_bounded_then_manual_get_can_recover(self):
        first = self.create()
        self.studio.process(first['id'])
        with patch.object(self.provider, 'query', return_value={'output': {'task_status': 'UNKNOWN'}}):
            for _ in range(3):
                self.studio.update(first['id'], next_check_at=0)
                self.studio.process(first['id'])
        job = self.studio.get(first['id'])
        self.assertEqual(job['status'], 'UNCERTAIN')
        self.assertEqual(job['recovery_attempts'], 3)
        self.assertIsNone(job['next_check_at'])
        self.assertTrue(self.studio.public(job)['recovery']['needs_operator'])
        self.studio.update(first['id'], last_checked_at=time.time()-20)
        self.studio.check_task(first['id'], 'owner-a')
        self.studio.process(first['id'])
        self.assertEqual(self.studio.get(first['id'])['status'], 'SUCCEEDED')
        self.assertEqual(self.provider.submits, 1)

    def test_no_id_requires_evidence_and_resolves_global_block_without_resubmit(self):
        first = self.create()
        self.provider.submit_error = app.ProviderError('connection lost')
        self.studio.process(first['id'])
        with self.assertRaises(app.StudioError):
            self.studio.check_task(first['id'], 'owner-a')
        with self.assertRaises(ValueError):
            task_recovery.resolve(self.studio.data_dir, first['id'], 'not-submitted', 'guess')
        with self.assertRaises(app.StudioError):
            self.create(request_id='blocked-before-review-12345')
        self.assertEqual(task_recovery.resolve(self.studio.data_dir, first['id'], 'not-submitted',
                         '离线模拟：在控制台核实该请求未创建模型任务'), 'REJECTED')
        self.assertEqual(self.create()['id'], first['id'])
        self.assertEqual(self.create(request_id='allowed-after-review-12345')['status'], 'SUBMITTING')
        with self.studio.db() as db:
            row = db.execute('SELECT * FROM job_reviews WHERE job_id=?', (first['id'],)).fetchone()
        self.assertEqual(row['next_status'], 'REJECTED')
        self.assertEqual(self.provider.submits, 1)

    def test_known_id_cannot_be_declared_not_submitted_and_unavailable_keeps_cost(self):
        first = self.create()
        self.studio.process(first['id'])
        self.studio.update(first['id'], status='UNCERTAIN', next_check_at=None)
        with self.assertRaises(ValueError):
            task_recovery.resolve(self.studio.data_dir, first['id'], 'not-submitted', '离线模拟：不能释放已绑定任务')
        task_recovery.resolve(self.studio.data_dir, first['id'], 'completed-unavailable', '离线模拟：原任务已生成，但文件已经过期不可获取')
        self.studio.daily_limit = 300
        with self.assertRaises(app.StudioError) as error:
            self.create(request_id='cost-must-remain-12345')
        self.assertEqual(error.exception.code, 'daily_limit')

    def test_operator_and_worker_share_a_lock_and_no_second_studio_is_constructed(self):
        first = self.create()
        self.studio.update(first['id'], status='UNCERTAIN')
        with task_recovery.job_lock(self.studio.data_dir, first['id']):
            self.studio.process(first['id'])
            with self.assertRaises(BlockingIOError):
                task_recovery.resolve(self.studio.data_dir, first['id'], 'failed', '离线模拟：查询原任务确认为失败')
        self.assertEqual(self.provider.submits, 0)

    def test_operator_can_bind_original_id_and_resume_get_only(self):
        first = self.create()
        self.studio.update(first['id'], status='UNCERTAIN')
        task_recovery.resolve(self.studio.data_dir, first['id'], 'attach',
                             '离线模拟：根据控制台请求记录匹配原任务', 'original-task-12345')
        self.studio.process(first['id'])
        self.assertEqual(self.studio.get(first['id'])['status'], 'SUCCEEDED')
        self.assertEqual((self.provider.submits, self.provider.queries), (0, 1))


class DownloadIntegrityChecks(unittest.TestCase):
    def test_short_or_interrupted_http_does_not_replace_original(self):
        import tempfile
        import urllib.request
        from http.client import IncompleteRead
        class Response(io.BytesIO):
            headers = {'Content-Length': str(len(FakeProvider.video()))}
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'test.mp4'
            destination.write_bytes(b'previous result')
            provider = object.__new__(app.Bailian)
            for body in (FakeProvider.video()[:4096], FakeProvider.video()+b'extra'):
                with patch.object(urllib.request, 'build_opener') as opener:
                    opener.return_value.open.return_value = Response(body)
                    with self.assertRaises(app.ProviderError):
                        provider.download('https://fake.oss-cn-beijing.aliyuncs.com/result.mp4', destination)
                self.assertEqual(destination.read_bytes(), b'previous result')
                self.assertEqual(list(Path(directory).glob('*.part')), [])
            with patch.object(urllib.request, 'build_opener') as opener:
                response = Response(b'')
                response.read = lambda _: (_ for _ in ()).throw(IncompleteRead(b'partial'))
                opener.return_value.open.return_value = response
                with self.assertRaises(app.ProviderError):
                    provider.download('https://fake.oss-cn-beijing.aliyuncs.com/result.mp4', destination)
            self.assertEqual(destination.read_bytes(), b'previous result')

    def test_decoding_rejects_wrong_duration_dimensions_and_corrupt_payload(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'test.mp4'
            content=FakeProvider.video()
            path.write_bytes(content)
            self.assertEqual(media.validate_media(path,5,(1280,720))['frames'],60)
            for duration,size in ((15,(1280,720)),(5,(720,1280))):
                with self.assertRaises(media.MediaValidationError):
                    media.validate_media(path,duration,size)
            # Preserve atom sizes/headers while destroying H.264 data: bounds alone cannot pass it.
            broken=bytearray(content); offset=0
            while offset<len(content):
                length,kind=struct.unpack('>I4s',content[offset:offset+8])
                if kind==b'mdat': broken[offset+8:offset+length]=b'\xff'*(length-8)
                offset+=length
            path.write_bytes(broken)
            media.validate_container(path)
            with self.assertRaises(media.MediaValidationError):media.validate_media(path,5,(1280,720))


class RecoveryHttpChecks(unittest.TestCase):
    setUp, tearDown = http_fixtures.ProductionHttpChecks.setUp, http_fixtures.ProductionHttpChecks.tearDown
    signed, call, browser, post = (http_fixtures.ProductionHttpChecks.signed, http_fixtures.ProductionHttpChecks.call,
                                 http_fixtures.ProductionHttpChecks.browser, http_fixtures.ProductionHttpChecks.post)
    def test_check_requires_csrf_owner_and_never_posts_model(self):
        browser=self.signed()
        status,_,body=self.post(browser,'/api/generations',{'prompt':'云海','aspect':'16:9','duration':5,'request_id':'recovery-http-test-12345'})
        self.assertEqual(status,202)
        job=json.loads(body)
        self.studio.process(job['id'])
        self.studio.update(job['id'],status='UNCERTAIN',next_check_at=None)
        path='/api/generations/'+job['id']+'/check'
        self.assertEqual(self.call(path,method='POST',cookie=browser[0])[0],403)
        other=self.signed('另一位核对用户')
        self.assertEqual(self.post(other,path,{})[0],404)
        status,_,body=self.post(browser,path,{})
        self.assertEqual(status,200)
        self.assertNotIn('task_id',json.loads(body))
        self.studio.process(job['id'])
        self.assertEqual(self.provider.submits,1)
        self.assertEqual(self.studio.get(job['id'])['status'],'SUCCEEDED')
