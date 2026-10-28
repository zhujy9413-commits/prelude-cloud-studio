"""Frozen inputs and wire fingerprints. All requests are intercepted locally."""
import base64
import io
import json
import sqlite3
import unittest
from unittest.mock import patch
import urllib.request

import generation_spec as record
import test_studio as fixtures

app=fixtures.app


class GenerationRecordChecks(unittest.TestCase):
    setUp,tearDown,create=fixtures.StudioChecks.setUp,fixtures.StudioChecks.tearDown,fixtures.StudioChecks.create

    def test_raw_original_frozen_wire_and_api_fields_are_recorded_before_submit(self):
        original='  一位女子缓慢转身\n保持同一人物  '
        job=self.create(prompt=original,parameters={'prompt_extend':False,'seed':1},model='untrusted-model')
        saved=job['generation_record'];spec=saved['snapshot']
        self.assertEqual(saved['state'],'recorded')
        self.assertEqual(spec['original_prompt'],original)
        self.assertEqual(spec['submitted_prompt'],original.strip())
        self.assertTrue(spec['parameters']['prompt_extend'])
        self.assertNotIn('seed',spec['parameters'])
        self.assertIsNone(saved['submission_started_at'])
        self.assertEqual(saved['request_sha256'],record.digest(record.encoded(record.payload(spec))))
        self.assertEqual(self.provider.submits,0)
        self.studio.process(job['id'])
        self.assertIsNotNone(self.studio.public(self.studio.get(job['id']))['generation_record']['submission_started_at'])

    def test_payload_uses_frozen_settings_even_if_later_defaults_change(self):
        job=self.create()
        original=self.studio.get(job['id'])
        provider=object.__new__(app.Bailian)
        with patch.object(record,'snapshot',side_effect=AssertionError('Defaults must not be recomputed')),patch.object(provider,'request',return_value={}) as request:
            provider.submit(original)
        sent=request.call_args.args[1]
        self.assertEqual(request.call_args.args[0],record.API_PATH)
        self.assertEqual(record.digest(record.encoded(sent)),original['request_fingerprint'])
        self.assertEqual(sent['parameters'],json.loads(original['request_spec'])['parameters'])

    def test_database_prevents_rewriting_the_original_record_after_creation(self):
        job=self.create();before=job['generation_record']
        with self.assertRaises(sqlite3.IntegrityError):
            self.studio.update(job['id'],request_spec='{}')
        self.studio.rename(job['id'],'owner-a','可重命名但不改生成记录')
        self.studio.manage(job['id'],'owner-a','favorite',{'favorite':True})
        self.assertEqual(self.studio.public(self.studio.get(job['id']))['generation_record'],before)
        self.assertEqual(self.create()['generation_record'],before)

    def test_changed_reference_or_job_cannot_be_sent_under_an_old_record(self):
        job=self.create(reference_preset='city')
        original=self.studio.get(job['id']);spec=json.loads(original['request_spec'])
        content=(app.ROOT/'dist/assets/city.webp').read_bytes()
        self.assertEqual(spec['reference']['sha256'],record.digest(content))
        self.assertNotIn('base64',original['request_spec'])
        with self.assertRaises(ValueError):record.frozen_payload(original,b'changed image')
        with self.assertRaises(ValueError):record.frozen_payload({**original,'prompt':'changed prompt'},content)
        with patch.object(app.ROOT.__class__,'read_bytes',return_value=b'changed image'):
            self.studio.process(job['id'])
        self.assertEqual(self.provider.submits,0)
        self.assertEqual(self.studio.get(job['id'])['status'],'REJECTED')

    def test_uploaded_image_hash_matches_exact_provider_input_without_saving_image_data(self):
        image=fixtures.StudioChecks.image((800,600))
        ref=self.studio.upload_reference('owner-a',image,'image/png','reference.png')
        job=self.create(reference_id=ref['id'],aspect=ref['aspect'])
        private=self.studio.get(job['id']);content=(self.studio.reference_dir/(ref['id']+'.jpg')).read_bytes()
        payload=record.frozen_payload(private,content)
        self.assertEqual(base64.b64decode(payload['input']['img_url'].split(',',1)[1]),content)
        self.assertEqual(private['request_fingerprint'],record.digest(record.encoded(payload)))
        self.assertNotIn('img_url',private['request_spec'])

    def test_historical_tasks_stay_explicitly_unrecorded(self):
        job=self.create();self.studio.process(job['id']);self.studio.process(job['id'])
        with self.studio.db() as db:
            # Simulate a pre-feature row without manufacturing historical parameter values.
            db.execute('DROP TRIGGER frozen_generation_record')
            db.execute('UPDATE jobs SET request_spec=NULL,request_fingerprint=NULL,request_recorded_at=NULL WHERE id=?',(job['id'],))
        restarted=app.Studio(self.studio.data_dir,self.provider)
        public=restarted.public(restarted.get(job['id']))
        self.assertEqual(public['generation_record']['state'],'legacy_unrecorded')
        self.assertIsNone(public['generation_record']['snapshot'])
        self.assertEqual(public['prompt'],self.payload['prompt'])
        self.assertEqual(self.provider.submits,1)

    def test_wire_fingerprint_is_for_actual_request_bytes_and_never_includes_headers(self):
        job=self.create();private=self.studio.get(job['id']);provider=object.__new__(app.Bailian)
        provider.key='offline-unit-credential';provider.host='dashscope.aliyuncs.com'
        response=io.BytesIO(b'{"output":{"task_id":"offline-task-12345"}}')
        with patch.object(urllib.request,'build_opener') as opener:
            opener.return_value.open.return_value=response
            provider.submit(private)
        request=opener.return_value.open.call_args.args[0]
        self.assertEqual(record.digest(request.data),private['request_fingerprint'])
        self.assertNotIn(provider.key,private['request_spec'])
        self.assertNotIn('Authorization',private['request_spec'])
