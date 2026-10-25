"""Offline checks: no provider requests, credentials, or charges."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('cloud_job', Path(__file__).with_name('generate_cloudscape.py'))
job = importlib.util.module_from_spec(spec)
spec.loader.exec_module(job)


class GenerationChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        production = self.root / 'video-production'
        production.mkdir()
        (production / 'cloudscape-start-frame.png').write_bytes(b'\x89PNG\r\n\x1a\n' + b'fixture')
        (production / 'cloudscape-video-brief.txt').write_text('图生视频提示词\nClouds billow.\n\n验收\nKeep the camera steady.')
        self.root_patch = patch.object(job, 'ROOT', self.root)
        self.state_patch = patch.object(job, 'STATE', production / '.state/cloudscape-task.json')
        self.root_patch.start()
        self.state_patch.start()
        self.settings = patch.object(job, 'config', return_value=('test-only-key', 'ws-test.cn-beijing.maas.aliyuncs.com'))
        self.settings.start()

    def tearDown(self):
        self.settings.stop()
        self.state_patch.stop()
        self.root_patch.stop()
        self.temp.cleanup()

    def test_over_budget_never_contacts_provider(self):
        with patch.object(job, 'api') as api, self.assertRaises(job.JobError):
            job.submit('1080P', 10, '1.50')
        api.assert_not_called()
        self.assertFalse(job.STATE.exists())

    def test_uncertain_submission_is_not_repeated(self):
        with patch.object(job, 'api', side_effect=job.JobError('timeout')) as api:
            with self.assertRaises(job.JobError):
                job.submit('720P', 10, '1.50')
            with self.assertRaises(job.JobError):
                job.submit('720P', 10, '1.50')
            self.assertEqual(api.call_count, 1)
        self.assertEqual(job.read_state()['status'], 'SUBMISSION_UNCERTAIN')
        self.assertNotIn('test-only-key', job.STATE.read_text())

    def test_submit_is_silent_and_separate_from_query(self):
        with patch.object(job, 'api', return_value={'output': {'task_id': 'task-123456789', 'task_status': 'PENDING'}}) as api:
            result = job.submit('720P', 10, '1.50')
            payload = api.call_args.args[3]
            self.assertFalse(payload['parameters']['audio'])
            self.assertEqual(payload['parameters']['duration'], 10)
            self.assertEqual(payload['parameters']['resolution'], '720P')
            self.assertTrue(payload['input']['img_url'].startswith('data:image/png;base64,'))
        self.assertEqual(result['task_id'], 'task-123456789')
        with patch.object(job, 'api', return_value={'output': {'task_status': 'SUCCEEDED', 'video_url': 'https://result.oss-cn-beijing.aliyuncs.com/video.mp4?private=example'}}) as api:
            result = job.query()
            self.assertEqual(len(api.call_args.args), 3)  # GET, without a new submission.
        self.assertEqual(result['status'], 'SUCCEEDED')
        self.assertNotIn('video_url', result)

    def test_rejected_key_does_not_create_or_retry_a_task(self):
        with patch.object(job, 'api', side_effect=job.ApiRejection(401)) as api:
            with self.assertRaises(job.ApiRejection):
                job.submit('720P', 10, '1.50')
            with self.assertRaises(job.JobError):
                job.submit('720P', 10, '1.50')
            self.assertEqual(api.call_count, 1)
        self.assertEqual(job.read_state()['status'], 'SUBMISSION_REJECTED')
        self.assertEqual(job.read_state()['http_status'], 401)
        self.assertNotIn('task_id', job.read_state())

    def test_result_url_cannot_send_key_to_an_unrelated_host(self):
        job.save_state({'status': 'SUCCEEDED', 'video_url': 'https://example.org/video.mp4'})
        with patch.object(job.urllib.request, 'urlopen') as open_url, self.assertRaises(job.JobError):
            job.download()
        open_url.assert_not_called()


if __name__ == '__main__':
    unittest.main()
