"""Playback derivatives, private revalidation, and original preservation; no model calls."""
import hashlib
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import media
import test_production as fixtures


class PlaybackAccessChecks(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ProductionHttpChecks()
        self.f.setUp()

    def tearDown(self):
        self.f.tearDown()

    def completed(self):
        import json
        browser = self.f.signed()
        status, _, body = self.f.post(browser, '/api/generations', {
            'prompt': '一片缓慢移动的云', 'aspect': '16:9', 'duration': 5,
            'request_id': 'playback-test-request-12345'})
        self.assertEqual(status, 202)
        job = json.loads(body)
        self.f.studio.process(job['id'])
        self.f.studio.process(job['id'])
        return browser, job

    def test_private_derivatives_range_cache_logout_and_trash(self):
        browser, job = self.completed()
        root = self.f.studio.playback_dir
        content = b'\x00\x00\x00\x20ftyp' + bytes(120)
        (root / (job['id'] + '.mp4')).write_bytes(content)
        (root / (job['id'] + '.jpg')).write_bytes(b'poster')
        public = self.f.studio.public(self.f.studio.get(job['id']))
        self.assertTrue(public['playback_optimized'])
        self.assertEqual(public['video_url'], '/api/generations/' + job['id'] + '/video')
        for path in (public['playback_url'], public['poster_url']):
            status, headers, body = self.f.call(path, cookie=browser[0])
            self.assertEqual(status, 200)
            self.assertEqual(headers['Cache-Control'], 'private, no-cache')
            self.assertEqual(self.f.call(path, cookie=browser[0], headers={'If-None-Match': headers['ETag']})[0], 304)
            self.assertEqual(self.f.call(path, headers={'If-None-Match': headers['ETag']})[0], 404)
        path = public['playback_url']
        download = self.f.call(path + '?download=1', cookie=browser[0])
        self.assertIn('prelude-flow-', download[1]['Content-Disposition'])
        self.assertEqual(download[2], content)
        self.assertEqual(self.f.call(path, cookie=browser[0], headers={'Range': 'bytes=4-11'})[2], content[4:12])
        self.assertEqual(self.f.call(path, cookie=browser[0], headers={'Range': 'bytes=9999-'})[0], 416)
        self.assertEqual(self.f.call(path, cookie=browser[0], headers={'Range': 'bytes=4-11', 'If-Range': 'old'})[0], 200)
        self.assertEqual(self.f.call(path, method='HEAD', cookie=browser[0])[2], b'')
        other = self.f.signed('另一位')
        self.assertEqual(self.f.call(path, cookie=other[0])[0], 404)
        self.assertEqual(self.f.post(browser, '/api/generations/' + job['id'] + '/trash', {})[0], 200)
        self.assertEqual(self.f.call(path, cookie=browser[0])[0], 404)
        self.assertEqual(self.f.post(browser, '/api/generations/' + job['id'] + '/restore', {})[0], 200)
        self.assertEqual(self.f.post(browser, '/api/access/logout', {})[0], 200)
        self.assertEqual(self.f.call(path, cookie=browser[0])[0], 404)

    def test_public_background_cache_never_sets_a_shared_cookie(self):
        status, headers, _ = self.f.call('/assets/cloudscape-loop.mp4', headers={'Range': 'bytes=0-31'})
        self.assertEqual(status, 206)
        self.assertTrue(headers['Cache-Control'].startswith('public'))
        self.assertNotIn('Set-Cookie', headers)
        self.assertEqual(self.f.call('/assets/cloudscape-loop.mp4', headers={'If-None-Match': headers['ETag']})[0], 304)
        self.assertEqual(self.f.call('/api/bootstrap')[1]['Cache-Control'], 'no-store')

    def test_derivative_failure_keeps_success_and_never_resubmits(self):
        with patch('server.prepare_media', side_effect=OSError('local encoder unavailable')):
            _, job = self.completed()
        self.assertEqual(self.f.studio.get(job['id'])['status'], 'SUCCEEDED')
        self.assertEqual(self.f.provider.submits, 1)
        public = self.f.studio.public(self.f.studio.get(job['id']))
        self.assertEqual(public['playback_url'], public['video_url'])
        self.assertFalse(public['playback_optimized'])

    def test_incremental_stream_is_private_and_downloads_keep_exact_original(self):
        browser, job = self.completed()
        public = self.f.studio.public(self.f.studio.get(job['id']))
        self.assertIsNotNone(public['stream_type'])
        path = public['stream_url']
        status, headers, content = self.f.call(path, cookie=browser[0])
        self.assertEqual(status, 200)
        self.assertEqual(headers['Cache-Control'], 'private, no-cache')
        self.assertEqual(self.f.call(path)[0], 404)
        self.assertEqual(self.f.call(path, cookie=browser[0], headers={'Range': 'bytes=0-31'})[2], content[:32])
        original = (self.f.studio.media_dir / (job['id']+'.mp4')).read_bytes()
        result = self.f.call(public['video_url']+'?download=1', cookie=browser[0])
        self.assertEqual(result[2], original)
        self.assertIn('prelude-original-',result[1]['Content-Disposition'])
        self.assertEqual(self.f.post(browser,'/api/generations/'+job['id']+'/trash',{})[0],200)
        self.assertEqual(self.f.call(path,cookie=browser[0])[0],404)


class EncoderChecks(unittest.TestCase):
    @unittest.skipUnless(media.encoder(), 'FFmpeg is optional; original playback remains available')
    def test_real_encoder_keeps_original_and_places_moov_before_video_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / 'test.mp4'
            subprocess.run([media.encoder(), '-v', 'error', '-f', 'lavfi', '-i',
                'testsrc2=size=640x360:rate=30:duration=1', '-c:v', 'libx264', '-preset', 'ultrafast',
                '-crf', '10', '-threads', '2', str(original)], check=True, timeout=20)
            before = hashlib.sha256(original.read_bytes()).digest()
            self.assertTrue(media.prepare_media(original, root / 'playback'))
            preview = root / 'playback/test.mp4'
            self.assertLess(preview.stat().st_size, original.stat().st_size)
            self.assertEqual(hashlib.sha256(original.read_bytes()).digest(), before)
            self.assertEqual(preview.stat().st_mode & 0o777, 0o600)
            self.assertTrue((root / 'playback/test.jpg').is_file())
            data = preview.read_bytes()
            offset, atoms = 0, []
            while offset + 8 <= len(data):
                size, kind = struct.unpack('>I4s', data[offset:offset+8])
                if size < 8:
                    break
                atoms.append(kind)
                offset += size
            self.assertLess(atoms.index(b'moov'), atoms.index(b'mdat'))
            subprocess.run([media.encoder(), '-v', 'error', '-i', str(preview), '-f', 'null', '-'], check=True, timeout=20)

    @unittest.skipUnless(media.encoder(), 'FFmpeg required for incremental delivery')
    def test_fragmenting_keeps_encoded_video_and_audio_packets(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);original=root/'test.mp4'
            subprocess.run([media.encoder(),'-v','error','-f','lavfi','-i','testsrc2=size=640x360:rate=30:duration=3',
                '-f','lavfi','-i','sine=frequency=440:duration=3','-c:v','libx264','-threads','2','-c:a','aac',str(original)],check=True,timeout=20)
            digest=hashlib.sha256(original.read_bytes()).digest()
            self.assertTrue(media.prepare_stream(original,root/'streams'))
            stream=root/'streams/test.mp4'
            self.assertIsNotNone(media.stream_type(str(stream),stream.stat().st_mtime_ns))
            self.assertEqual(media.validate_media(stream,3)['frames'],90)
            self.assertEqual(stream.stat().st_mode&0o777,0o600)
            for selector in ('0:v:0','0:a:0'):
                def packets(path):
                    return subprocess.check_output([media.encoder(),'-v','error','-i',str(path),'-map',selector,
                        '-c','copy','-f','streamhash','-hash','sha256','-'],timeout=20).split(b'=')[-1].strip()
                self.assertEqual(packets(original),packets(stream))
            modified=stream.stat().st_mtime_ns
            self.assertTrue(media.prepare_stream(original,root/'streams'))
            self.assertEqual(stream.stat().st_mtime_ns,modified)
            self.assertEqual(hashlib.sha256(original.read_bytes()).digest(),digest)
