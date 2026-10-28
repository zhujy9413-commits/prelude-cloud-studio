"""Invitation, cross-device ownership, and budget checks. All providers are fake."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from PIL import Image
import test_studio as fixtures

app, FakeProvider = fixtures.app, fixtures.FakeProvider


class IdentityChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.provider = FakeProvider()
        self.studio = app.Studio(Path(self.temp.name), self.provider, invite_only=True)
        self.access = self.studio.access
        self.password = 'A memorable phrase 2026'
        self.payload = {'prompt':'安静的云海缓慢翻卷','aspect':'16:9','duration':5,'request_id':'access-request-123456789'}

    def tearDown(self):
        self.temp.cleanup()

    def activate(self, name='小雨', code=None, owner='guest-owner', seconds=10):
        if code is None:
            _, code = self.access.invite(seconds)
        return self.access.authenticate('activate', {'name':name,'password':self.password,'invitation':code}, owner, owner, '127.0.0.1')

    def test_activation_migrates_existing_frames_video_and_metadata_without_spending_allowance(self):
        self.studio.invite_only = False
        output = io.BytesIO(); Image.new('RGB',(640,360),'navy').save(output,format='PNG')
        frame = self.studio.upload_reference('guest-owner',output.getvalue(),'image/png','海岸.png')
        job = self.studio.create('guest-owner',{**self.payload,'reference_id':frame['id']})
        self.studio.process(job['id']); self.studio.process(job['id'])
        self.studio.rename(job['id'],'guest-owner','我的第一幕')
        self.studio.manage(job['id'],'guest-owner','favorite',{'favorite':True})
        self.studio.invite_only = True
        token, account = self.activate()
        self.assertEqual(self.access.quota(account)['remaining_seconds'],10)
        saved = self.studio.list_jobs(account)[0]
        self.assertEqual(saved['title'],'我的第一幕'); self.assertTrue(saved['favorite'])
        self.assertEqual(saved['reference_id'],frame['id'])
        self.assertEqual(self.studio.get_reference(frame['id'],account)['name'],'海岸.png')
        self.assertEqual(self.studio.list_jobs('guest-owner'),[])
        self.assertEqual(self.access.account_for(hashlib.sha256(token.encode()).hexdigest())['id'],account)
        self.assertEqual(self.provider.submits,1)
        with self.access.db() as db:
            row = db.execute('SELECT * FROM accounts').fetchone()
            self.assertNotEqual(row['password_hash'],self.password)
            self.assertNotEqual(row['password_hash'],hashlib.sha256(self.password.encode()).hexdigest())

    def test_same_invite_can_only_create_one_account_under_concurrency(self):
        _, code = self.access.invite(15)
        def redeem(i):
            try: return self.activate(name='创作'+str(i),code=code)[1]
            except app.AccessError as error: return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(redeem,range(2)))
        self.assertEqual(sum(value.startswith('account-') for value in result),1)
        self.assertIn('invite_invalid',result)

    def test_expired_revoked_used_and_invalid_invites_never_create_accounts(self):
        for change in ('expired','revoked','used'):
            identifier, code = self.access.invite()
            with self.access.db() as db:
                if change == 'expired': db.execute('UPDATE invitations SET expires_at=0 WHERE id=?',(identifier,))
                if change == 'revoked': db.execute('UPDATE invitations SET revoked=1 WHERE id=?',(identifier,))
                if change == 'used': db.execute("UPDATE invitations SET used_by='someone' WHERE id=?",(identifier,))
            with self.assertRaises(app.AccessError) as error: self.activate(name=change,code=code)
            self.assertEqual(error.exception.code,'invite_invalid')
        with self.assertRaises(app.AccessError): self.activate(code='unknown')
        with self.access.db() as db: self.assertEqual(db.execute('SELECT COUNT(*) FROM accounts').fetchone()[0],0)
        self.assertEqual(self.provider.submits,0)

    def test_unicode_name_canonicalization_does_not_create_duplicate_identity(self):
        _, account = self.activate(name='Ａlice')
        _, code = self.access.invite()
        with self.assertRaises(app.AccessError) as error: self.activate(name='alice',code=code)
        self.assertEqual(error.exception.code,'name_taken')
        _, other = self.activate(name='另一位',code=code,owner='other')
        self.assertNotEqual(account,other)

    def test_personal_seconds_reserved_once_failure_releases_uncertain_and_trash_do_not(self):
        _, account = self.activate(seconds=5)
        job = self.studio.create(account,self.payload)
        self.assertEqual(self.access.quota(account)['remaining_seconds'],0)
        self.assertEqual(self.studio.create(account,self.payload)['id'],job['id'])
        self.assertEqual(self.access.quota(account)['used_seconds'],5)
        self.studio.update(job['id'],status='UNCERTAIN')
        self.assertEqual(self.access.quota(account)['remaining_seconds'],0)
        self.studio.update(job['id'],status='FAILED')
        self.assertEqual(self.access.quota(account)['remaining_seconds'],5)
        second = self.studio.create(account,{**self.payload,'request_id':'access-second-123456789'})
        self.studio.process(second['id']); self.studio.process(second['id'])
        self.studio.manage(second['id'],account,'trash',{})
        self.assertEqual(self.access.quota(account)['remaining_seconds'],0)
        with self.assertRaises(app.StudioError) as error:
            self.studio.create(account,{**self.payload,'request_id':'access-third-123456789'})
        self.assertEqual(error.exception.code,'personal_limit')

    def test_accounts_still_share_daily_budget_and_global_active_lock(self):
        _, a = self.activate(name='小雨',seconds=30)
        _, b = self.activate(name='小风',owner='other',seconds=30)
        first = self.studio.create(a,self.payload)
        with self.assertRaises(app.StudioError) as error: self.studio.create(b,{**self.payload,'request_id':'other-request-12345678'})
        self.assertEqual(error.exception.code,'busy')
        self.studio.process(first['id']); self.studio.process(first['id'])
        self.studio.daily_limit = 300
        with self.assertRaises(app.StudioError) as error: self.studio.create(b,{**self.payload,'request_id':'other-request-12345678'})
        self.assertEqual(error.exception.code,'daily_limit')

    def test_sessions_and_account_ownership_survive_restart_and_expire(self):
        token, account = self.activate()
        self.studio.create(account,self.payload)
        restarted = app.Studio(self.studio.data_dir,self.provider,invite_only=True)
        hashed = hashlib.sha256(token.encode()).hexdigest()
        self.assertEqual(restarted.access.account_for(hashed)['id'],account)
        self.assertEqual(restarted.list_jobs(account)[0]['status'],'UNCERTAIN')
        with self.access.db() as db: db.execute('UPDATE account_sessions SET expires_at=0')
        self.assertIsNone(self.access.account_for(hashed))
        self.assertEqual(self.provider.submits,0)

    def test_wrong_password_rate_limit_persists_across_restarts(self):
        self.activate()
        for _ in range(6):
            with self.assertRaises(app.AccessError) as error:
                self.access.authenticate('login',{'name':'小雨','password':'a wrong password phrase'},'other','other','another-peer')
            self.assertEqual(error.exception.code,'login_invalid')
        reopened = type(self.access)(self.studio.db_path)
        with self.assertRaises(app.AccessError) as error:
            reopened.authenticate('login',{'name':'小雨','password':self.password},'other','other','yet-another-peer')
        self.assertEqual(error.exception.code,'login_limited')
        with reopened.db() as db: db.execute('UPDATE access_attempts SET attempted_at=?',(time.time()-901,))
        self.assertTrue(reopened.authenticate('login',{'name':'小雨','password':self.password},'other','other','peer')[1].startswith('account-'))

    def test_invite_only_guest_cannot_reserve_provider_budget(self):
        with self.assertRaises(app.AccessError) as error: self.studio.create('guest',self.payload)
        self.assertEqual(error.exception.code,'login_required')
        self.assertEqual(self.studio.list_jobs('guest'),[])
        self.assertEqual(self.provider.submits,0)


class IdentityHttpChecks(unittest.TestCase):
    call = fixtures.HttpChecks.call

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.provider = FakeProvider()
        self.studio = app.Studio(Path(self.temp.name),self.provider,invite_only=True)
        self.http = app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler); self.http.studio = self.studio
        self.thread = threading.Thread(target=self.http.serve_forever,daemon=True); self.thread.start()

    def tearDown(self):
        self.http.shutdown(); self.http.server_close(); self.thread.join(); self.temp.cleanup()

    def browser(self, cookie=None):
        _, headers, body = self.call('/api/bootstrap',cookie=cookie)
        return (headers.get('Set-Cookie','').split(';')[0] or cookie), json.loads(body)

    def auth(self, browser):
        return {'Origin':f'http://127.0.0.1:{self.http.server_port}','X-Prelude-CSRF':browser[1]['csrf']}

    def post(self, browser, path, data):
        return self.call(path,'POST',data,self.auth(browser),browser[0])

    def test_activation_login_on_second_device_logout_and_owner_isolation(self):
        first = self.browser(); _, code = self.studio.access.invite(10)
        payload = {'name':'小雨','password':'a memorable password phrase','invitation':code}
        self.assertFalse(first[1]['access']['signed_in'])
        self.assertEqual(self.call('/api/access/activate','POST',payload,{'Origin':self.auth(first)['Origin']},first[0])[0],403)
        status, headers, _ = self.post(first,'/api/access/activate',payload)
        self.assertEqual(status,200); self.assertNotEqual(headers['Set-Cookie'].split(';')[0],first[0])
        signed = self.browser(headers['Set-Cookie'].split(';')[0])
        self.assertTrue(signed[1]['access']['signed_in'])
        self.assertNotEqual(signed[1]['csrf'],first[1]['csrf'])
        job_payload = {'request_id':'http-access-request-1234567','prompt':'安静的海岸','aspect':'16:9','duration':5}
        self.assertEqual(self.call('/api/generations','POST',job_payload,self.auth(first),signed[0])[0],403)
        status, _, body = self.post(signed,'/api/generations',job_payload)
        self.assertEqual(status,202); job = json.loads(body)
        self.studio.process(job['id']); self.studio.process(job['id'])
        second = self.browser()
        status, headers, _ = self.post(second,'/api/access/login',payload)
        self.assertEqual(status,200)
        device2 = self.browser(headers['Set-Cookie'].split(';')[0])
        self.assertEqual(device2[1]['jobs'][0]['id'],job['id'])
        self.assertEqual(device2[1]['access']['quota']['remaining_seconds'],5)
        self.assertEqual(self.call(job['video_url'] or '/api/generations/'+job['id']+'/video',cookie=device2[0])[0],200)
        self.assertEqual(self.call('/api/generations/'+job['id'],cookie=first[0])[0],404)
        self.assertEqual(self.call('/api/generations/'+job['id'],cookie=signed[0])[0],200)
        self.assertEqual(self.post(device2,'/api/access/logout',{})[0],200)
        self.assertEqual(self.call('/api/generations/'+job['id'],cookie=device2[0])[0],404)
        self.assertEqual(self.call('/api/generations/'+job['id'],cookie=signed[0])[0],200)

    def test_unauthenticated_mutation_upload_and_csrf_fail_without_generation(self):
        guest = self.browser()
        for path in ('/api/generations','/api/references'):
            status, _, body = self.post(guest,path,{})
            self.assertEqual(status,401); self.assertEqual(json.loads(body)['code'],'login_required')
        self.assertEqual(self.provider.submits,0)

    def test_other_account_cannot_read_or_edit_frame_video_or_collection(self):
        first = self.browser(); _, code = self.studio.access.invite(10)
        _, headers, _ = self.post(first,'/api/access/activate',{'name':'小雨','password':'a memorable password phrase','invitation':code})
        signed = self.browser(headers['Set-Cookie'].split(';')[0])
        account = signed[1]['access']['account_id']
        image = io.BytesIO(); Image.new('RGB',(640,360),'navy').save(image,format='PNG')
        frame = self.studio.upload_reference(account,image.getvalue(),'image/png','frame.png')
        job = self.studio.create(account,{'request_id':'http-owned-request-1234567','prompt':'缓慢涌动','aspect':'16:9','duration':5,'reference_id':frame['id']})
        self.studio.process(job['id']); self.studio.process(job['id'])
        stranger = self.browser(); _, code = self.studio.access.invite()
        _, headers, _ = self.post(stranger,'/api/access/activate',{'name':'小风','password':'another password phrase','invitation':code})
        stranger = self.browser(headers['Set-Cookie'].split(';')[0])
        for path in (frame['image_url'],'/api/generations/'+job['id'],'/api/generations/'+job['id']+'/video'):
            self.assertEqual(self.call(path,cookie=stranger[0])[0],404)
        for action in ('favorite','rename','trash','restore'):
            self.assertEqual(self.post(stranger,'/api/generations/'+job['id']+'/'+action,{'favorite':True,'title':'别人'})[0],404)


if __name__ == '__main__':
    unittest.main()
