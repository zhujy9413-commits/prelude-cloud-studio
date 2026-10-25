#!/usr/bin/env python3
"""Local Prelude studio: same-origin UI, persistent jobs, server-side Bailian calls."""
import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import fcntl
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'video-production'))
from generate_cloudscape import config, JobError, NoRedirect

MODEL = 'wan2.6-t2v'
IMAGE_MODEL = 'wan2.6-i2v-flash'
REFERENCES = {'cloud': 'cloudscape.webp', 'city': 'city.webp', 'lunar': 'lunar.webp'}
SIZES = {'16:9': '1280*720', '9:16': '720*1280', '1:1': '960*960'}
ACTIVE = ('SUBMITTING', 'PENDING', 'RUNNING', 'DOWNLOADING', 'UNCERTAIN')
TERMINAL = ('SUCCEEDED', 'FAILED', 'REJECTED', 'UNCERTAIN', 'UNAVAILABLE')


class StudioError(Exception):
    def __init__(self, message, status=400, code='invalid_request'):
        super().__init__(message)
        self.status, self.code = status, code


class ProviderError(Exception):
    def __init__(self, message, rejected=False):
        super().__init__(message)
        self.rejected = rejected


def provider_message(code):
    code = str(code).lower()
    if any(word in code for word in ('inspection', 'sensitive', 'inappropriate', 'contentfilter')):
        return '这段描述未通过内容审核，请调整后再试'
    if any(word in code for word in ('apikey', 'accessdenied', 'unauthorized')):
        return '视频服务认证失败，请检查服务端密钥与模型权限'
    if any(word in code for word in ('arrear', 'balance', 'quota', 'allocation')):
        return '可用额度不足或账号状态受限，请检查百炼账户'
    if any(word in code for word in ('throttl', 'limit', 'overload')):
        return '模型服务繁忙，请稍后再试'
    return '模型未能完成这次生成，请稍后调整描述再试'


class Bailian:
    def __init__(self):
        self.key, self.host = config()

    def request(self, path, payload=None):
        headers = {'Authorization': 'Bearer ' + self.key, 'Accept': 'application/json'}
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode()
            headers.update({'Content-Type': 'application/json', 'X-DashScope-Async': 'enable'})
        request = urllib.request.Request('https://' + self.host + path, data=data, headers=headers)
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=45) as response:
                body = json.loads(response.read(2 * 1024 * 1024))
            if not isinstance(body, dict):
                raise ValueError('Unexpected response')
            return body
        except urllib.error.HTTPError as error:
            try:
                body = json.loads(error.read(65536))
                code = body.get('code', '') if isinstance(body, dict) else ''
            except (ValueError, OSError):
                code = ''
            raise ProviderError(provider_message(code), rejected=error.code in (400, 401, 403, 404, 422, 429)) from None
        except (urllib.error.URLError, TimeoutError, ValueError, OSError, JobError):
            raise ProviderError('服务连接暂时中断，正在核对原任务，请勿重复提交') from None

    def submit(self, job):
        reference = job.get('reference_preset')
        if reference:
            # Only bundled reference frames are accepted; clients cannot supply paths or URLs.
            image = ROOT / 'dist/assets' / REFERENCES[reference]
            return self.request('/api/v1/services/aigc/video-generation/video-synthesis', {
                'model': IMAGE_MODEL,
                'input': {'prompt': job['prompt'], 'img_url': 'data:image/webp;base64,' + base64.b64encode(image.read_bytes()).decode()},
                'parameters': {'resolution': '720P', 'duration': job['duration'], 'audio': True,
                               'prompt_extend': True, 'shot_type': 'single', 'watermark': False},
            })
        return self.request('/api/v1/services/aigc/video-generation/video-synthesis', {
            'model': MODEL, 'input': {'prompt': job['prompt']},
            'parameters': {'size': SIZES[job['aspect']], 'duration': job['duration'],
                           'prompt_extend': True, 'shot_type': 'single', 'watermark': False},
        })

    def query(self, task_id):
        return self.request('/api/v1/tasks/' + task_id)

    def download(self, url, destination):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != 'https' or parsed.username or parsed.password or not (
            (parsed.hostname or '').endswith('.aliyuncs.com') or (parsed.hostname or '').endswith('.alicdn.com')
        ):
            raise ProviderError('视频下载地址异常，已保留原任务供核对')
        fd, temp = tempfile.mkstemp(dir=destination.parent, suffix='.part')
        try:
            total = 0
            # No authorization header is sent to the result host. No redirects followed.
            with os.fdopen(fd, 'wb') as handle, urllib.request.build_opener(NoRedirect()).open(url, timeout=60) as response:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > 100 * 1024 * 1024:
                        raise ProviderError('视频文件超过本地保存上限，原任务已保留')
                    handle.write(chunk)
            with open(temp, 'rb') as handle:
                header = handle.read(32)
            if total < 32 or header[4:8] != b'ftyp':
                raise ProviderError('视频文件暂不可用，正在重新获取原结果')
            os.replace(temp, destination)
        except (urllib.error.URLError, OSError, JobError):
            raise ProviderError('视频已生成，正在重新获取原结果') from None
        finally:
            if os.path.exists(temp):
                os.unlink(temp)


class Studio:
    def __init__(self, data_dir, provider=None, daily_limit_cents=3000, poll_seconds=15):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.media_dir = self.data_dir / 'videos'
        self.media_dir.mkdir(exist_ok=True, mode=0o700)
        self.db_path = self.data_dir / 'studio.sqlite3'
        self.provider = provider
        self.daily_limit = daily_limit_cents
        self.poll_seconds = poll_seconds
        self.stop = threading.Event()
        self.wake = threading.Event()
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_id TEXT NOT NULL,
                prompt TEXT NOT NULL, aspect TEXT NOT NULL, duration INTEGER NOT NULL,
                cost_cents INTEGER NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                status TEXT NOT NULL, task_id TEXT, error TEXT NOT NULL DEFAULT '',
                UNIQUE(owner, request_id))''')
            db.execute('CREATE UNIQUE INDEX IF NOT EXISTS one_job_per_request ON jobs(request_id)')
            if 'title' not in {row['name'] for row in db.execute('PRAGMA table_info(jobs)')}:
                db.execute("ALTER TABLE jobs ADD COLUMN title TEXT NOT NULL DEFAULT ''")
            columns = {row['name'] for row in db.execute('PRAGMA table_info(jobs)')}
            if 'favorite' not in columns:
                db.execute('ALTER TABLE jobs ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0')
            if 'deleted_at' not in columns:
                db.execute('ALTER TABLE jobs ADD COLUMN deleted_at REAL')
            if 'model' not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN model TEXT NOT NULL DEFAULT 'wan2.6-t2v'")
            if 'reference_preset' not in columns:
                db.execute('ALTER TABLE jobs ADD COLUMN reference_preset TEXT')
            db.execute("UPDATE jobs SET status='UNCERTAIN', error=? WHERE status='SUBMITTING' AND task_id IS NULL",
                       ('提交时服务重启，结果需要人工核对；不会自动重新提交',))
        os.chmod(self.db_path, 0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.db_path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def list_jobs(self, owner, deleted=False):
        with self.db() as db:
            condition = 'IS NOT NULL' if deleted else 'IS NULL'
            return [self.public(dict(row)) for row in db.execute('SELECT * FROM jobs WHERE owner=? AND deleted_at ' + condition + ' ORDER BY created_at DESC', (owner,))]

    def get(self, job_id, owner=None):
        with self.db() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row or (owner is not None and row['owner'] != owner):
            raise StudioError('未找到这条作品', 404, 'not_found')
        return dict(row)

    def public(self, job):
        return {**{key: job[key] for key in ('id', 'request_id', 'title', 'prompt', 'aspect', 'duration', 'status', 'created_at', 'error')},
                'favorite': bool(job['favorite']), 'deleted_at': job['deleted_at'],
                'model': job['model'], 'reference_preset': job['reference_preset'],
                'reference_image': '/assets/' + REFERENCES[job['reference_preset']] if job['reference_preset'] in REFERENCES else None,
                'resolution': '720P', 'estimated_cost_cny': f"{job['cost_cents']/100:.2f}",
                'video_url': '/api/generations/' + job['id'] + '/video' if job['status'] == 'SUCCEEDED' and job['deleted_at'] is None else None}

    def create(self, owner, payload):
        if not isinstance(payload, dict):
            raise StudioError('提交内容格式不正确')
        prompt = payload.get('prompt')
        aspect, duration = payload.get('aspect'), payload.get('duration')
        reference = payload.get('reference_preset')
        request_id = payload.get('request_id', '')
        if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 1200:
            raise StudioError('请输入 1 到 1200 字的画面描述')
        prompt = prompt.strip()
        if not isinstance(aspect, str) or aspect not in SIZES or type(duration) is not int or not 2 <= duration <= 15:
            raise StudioError('请选择支持的画幅，并填写 2 到 15 之间的整数秒数')
        if reference is not None and (not isinstance(reference, str) or reference not in REFERENCES):
            raise StudioError('请选择工作室提供的参考首帧')
        if reference and aspect != '16:9':
            raise StudioError('当前示例首帧使用 16:9 横屏，请移除首帧后再调整画幅')
        if reference and not (ROOT / 'dist/assets' / REFERENCES[reference]).is_file():
            raise StudioError('参考首帧暂不可用，请移除后再试', 503, 'reference_unavailable')
        if not isinstance(request_id, str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,80}', request_id):
            raise StudioError('请求标识无效，请刷新后重试')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM jobs WHERE request_id=?', (request_id,)).fetchone()
            if old:
                if old['owner'] != owner:
                    raise StudioError('浏览器会话已改变，请先核对原作品；不会重复提交', 403, 'request_session_changed')
                if (old['prompt'], old['aspect'], old['duration'], old['reference_preset']) != (prompt, aspect, duration, reference):
                    raise StudioError('此请求已提交，请先核对原任务', 409, 'request_conflict')
                return self.public(dict(old))
            if self.provider is None:
                raise StudioError('服务端尚未配置百炼密钥', 503, 'not_configured')
            if db.execute('SELECT 1 FROM jobs WHERE status IN (?,?,?,?,?) LIMIT 1', ACTIVE).fetchone():
                raise StudioError('已有视频正在生成或待核对，请等待原任务完成', 409, 'busy')
            china = timezone(timedelta(hours=8))
            day_start = datetime.now(china).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            spent = db.execute("SELECT COALESCE(SUM(cost_cents),0) FROM jobs WHERE created_at>=? AND status NOT IN ('FAILED','REJECTED')", (day_start,)).fetchone()[0]
            cost = duration * (30 if reference else 60)
            if spent + cost > self.daily_limit:
                raise StudioError('今天的本地生成预算已达到上限，请明天继续', 429, 'daily_limit')
            now, job_id = time.time(), str(uuid.uuid4())
            db.execute('''INSERT INTO jobs(id,owner,request_id,prompt,aspect,duration,cost_cents,created_at,updated_at,status,model,reference_preset)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''', (job_id, owner, request_id, prompt, aspect, duration, cost, now, now, 'SUBMITTING', IMAGE_MODEL if reference else MODEL, reference))
        self.wake.set()
        return self.public(self.get(job_id))

    def update(self, job_id, **values):
        values['updated_at'] = time.time()
        with self.db() as db:
            db.execute('UPDATE jobs SET ' + ','.join(key + '=?' for key in values) + ' WHERE id=?', (*values.values(), job_id))

    def rename(self, job_id, owner, title):
        if self.get(job_id, owner)['deleted_at'] is not None:
            raise StudioError('请先从最近删除中恢复这条作品', 409, 'trashed')
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 60 or any(ord(char) < 32 for char in title):
            raise StudioError('请输入 1 到 60 字的作品名称')
        self.update(job_id, title=title.strip())
        return self.public(self.get(job_id, owner))

    def manage(self, job_id, owner, action, payload):
        if not isinstance(payload, dict):
            raise StudioError('提交内容格式不正确')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM jobs WHERE id=? AND owner=?', (job_id, owner)).fetchone()
            if row is None:
                raise StudioError('未找到这条作品', 404, 'not_found')
            if action == 'favorite':
                if type(payload.get('favorite')) is not bool:
                    raise StudioError('收藏状态必须为布尔值')
                if row['deleted_at'] is not None:
                    raise StudioError('请先恢复这条作品，再进行收藏', 409, 'trashed')
                db.execute('UPDATE jobs SET favorite=?, updated_at=? WHERE id=?', (int(payload['favorite']), time.time(), job_id))
            elif action == 'trash':
                if row['status'] not in TERMINAL or row['status'] == 'UNCERTAIN':
                    raise StudioError('请等这条任务结束后再删除，生成不会因此取消', 409, 'active_job')
                db.execute('UPDATE jobs SET deleted_at=COALESCE(deleted_at,?), updated_at=? WHERE id=?', (time.time(), time.time(), job_id))
            elif action == 'restore':
                db.execute('UPDATE jobs SET deleted_at=NULL, updated_at=? WHERE id=?', (time.time(), job_id))
            else:
                raise StudioError('不支持的作品操作')
        return self.public(self.get(job_id, owner))

    def process(self, job_id):
        job = self.get(job_id)
        if not self.provider:
            return
        if job['status'] == 'SUBMITTING':
            try:
                body = self.provider.submit(job)
                output = body.get('output', {})
                task_id = output.get('task_id')
                if not isinstance(task_id, str) or not re.fullmatch(r'[a-zA-Z0-9-]{10,100}', task_id):
                    if body.get('code'):
                        raise ProviderError(provider_message(body['code']), rejected=True)
                    raise ProviderError('未取得任务编号，需要人工核对，请勿重复提交')
                self.update(job_id, status='PENDING', task_id=task_id, error='')
            except ProviderError as error:
                self.update(job_id, status='REJECTED' if error.rejected else 'UNCERTAIN', error=str(error))
            return
        if not job['task_id'] or job['status'] in TERMINAL:
            return
        try:
            body = self.provider.query(job['task_id'])
            output = body.get('output', {})
            state = output.get('task_status')
            if state in ('PENDING', 'RUNNING'):
                self.update(job_id, status=state, error='')
            elif state == 'SUCCEEDED':
                self.update(job_id, status='DOWNLOADING', error='')
                self.provider.download(output.get('video_url', ''), self.media_dir / (job_id + '.mp4'))
                self.update(job_id, status='SUCCEEDED', error='')
            elif state in ('FAILED', 'CANCELED'):
                self.update(job_id, status='FAILED', error=provider_message(output.get('code', '')))
            elif state == 'UNKNOWN':
                self.update(job_id, status='UNCERTAIN', error='原任务状态暂不可确认，需要核对；不会自动重新生成')
            else:
                raise ProviderError('正在重新查询原任务，请勿重复提交')
        except ProviderError as error:
            self.update(job_id, error=str(error))

    def run_worker(self):
        while not self.stop.is_set():
            self.wake.clear()
            with self.db() as db:
                rows = db.execute("SELECT id FROM jobs WHERE status IN ('SUBMITTING','PENDING','RUNNING','DOWNLOADING')").fetchall()
            for row in rows:
                if self.stop.is_set():
                    break
                try:
                    self.process(row['id'])
                except Exception:
                    # Never log raw exceptions: they may contain a signed URL or user prompt.
                    job = self.get(row['id'])
                    self.update(row['id'], status='UNCERTAIN' if job['status'] == 'SUBMITTING' else job['status'], error='服务暂时中断，原任务已保留，请勿重复提交')
            self.wake.wait(self.poll_seconds)


class Handler(BaseHTTPRequestHandler):
    server_version = 'Prelude'

    def log_message(self, *_):
        pass  # No prompts, cookies, signed URLs, credentials, or request bodies in logs.

    def guard(self, mutation=False):
        host = self.headers.get('Host', '')
        allowed = {'127.0.0.1:' + str(self.server.server_port), 'localhost:' + str(self.server.server_port)}
        if host not in allowed:
            raise StudioError('无效的访问地址', 403, 'host_forbidden')
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + host:
            raise StudioError('请从本机工作室页面访问', 403, 'origin_forbidden')
        if mutation and origin != 'http://' + host:
            raise StudioError('请从工作室页面提交', 403, 'origin_required')
        public_navigation = (self.command in ('GET', 'HEAD') and
                             self.headers.get('Sec-Fetch-Mode') == 'navigate' and
                             self.headers.get('Sec-Fetch-Dest') == 'document' and
                             not urllib.parse.urlsplit(self.path).path.startswith('/api/'))
        if self.headers.get('Sec-Fetch-Site') == 'cross-site' and not public_navigation:
            raise StudioError('不支持跨站访问', 403, 'cross_site')

    def session(self):
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get('Cookie', ''))
        except cookies.CookieError:
            pass
        token = jar.get('prelude_session')
        token = token.value if token else ''
        self.new_cookie = None
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
            token = secrets.token_urlsafe(32)
            self.new_cookie = 'prelude_session=' + token + '; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000'
        self.owner = hashlib.sha256(token.encode()).hexdigest()
        self.csrf = hashlib.sha256((token + ':csrf').encode()).hexdigest()

    def common_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        self.send_header('Cache-Control', 'no-store')
        if getattr(self, 'new_cookie', None):
            self.send_header('Set-Cookie', self.new_cookie)

    def json_response(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.common_headers()
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def file_response(self, path, download=False):
        size = path.stat().st_size
        start, end, partial = 0, size - 1, False
        range_header = self.headers.get('Range')
        if range_header:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', range_header)
            if not match or not any(match.groups()):
                raise StudioError('无效的视频范围', 416, 'invalid_range')
            left, right = match.groups()
            if left:
                start, end = int(left), min(int(right), size - 1) if right else size - 1
            else:
                start = max(0, size - int(right))
            if start > end or start >= size:
                self.send_response(416)
                self.common_headers()
                self.send_header('Content-Range', 'bytes */' + str(size))
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            partial = True
        self.send_response(206 if partial else 200)
        self.common_headers()
        self.send_header('Content-Type', mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
        self.send_header('Content-Length', str(end - start + 1))
        self.send_header('Accept-Ranges', 'bytes')
        if partial:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        if download:
            self.send_header('Content-Disposition', 'attachment; filename="prelude-' + path.name + '"')
        self.end_headers()
        if self.command != 'HEAD':
            with path.open('rb') as handle:
                handle.seek(start)
                remaining = end - start + 1
                while remaining:
                    block = handle.read(min(256 * 1024, remaining))
                    if not block:
                        break
                    self.wfile.write(block)
                    remaining -= len(block)

    def dispatch(self):
        self.guard(self.command == 'POST')
        self.session()
        parsed = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parsed.path)
        studio = self.server.studio
        if self.command in ('GET', 'HEAD'):
            if path == '/api/bootstrap':
                return self.json_response({'csrf': self.csrf, 'ready': studio.provider is not None, 'model': MODEL,
                    'reference_supported': True, 'reference_model': IMAGE_MODEL,
                    'price_per_second_cny': '0.60', 'daily_limit_cny': f'{studio.daily_limit/100:.2f}', 'poll_seconds': studio.poll_seconds,
                    'jobs': studio.list_jobs(self.owner), 'deleted_jobs': studio.list_jobs(self.owner, deleted=True)})
            if path == '/api/generations':
                return self.json_response({'jobs': studio.list_jobs(self.owner, deleted=urllib.parse.parse_qs(parsed.query).get('deleted') == ['1'])})
            match = re.fullmatch(r'/api/generations/([a-f0-9-]{36})(/video)?', path)
            if match:
                job = studio.get(match[1], self.owner)
                if not match[2]:
                    return self.json_response(studio.public(job))
                media = studio.media_dir / (job['id'] + '.mp4')
                if job['deleted_at'] is not None or job['status'] != 'SUCCEEDED' or not media.is_file():
                    raise StudioError('视频还没有准备好', 404, 'not_ready')
                return self.file_response(media, download='download' in urllib.parse.parse_qs(parsed.query))
            if path.startswith('/api/'):
                raise StudioError('接口不存在', 404, 'not_found')
            if path == '/':
                path = '/index.html'
            target = (ROOT / 'dist' / path.lstrip('/')).resolve()
            public_root = (ROOT / 'dist').resolve()
            if public_root not in target.parents or any(part.startswith('.') for part in Path(path).parts) or not target.is_file():
                raise StudioError('页面不存在', 404, 'not_found')
            return self.file_response(target)
        rename_match = re.fullmatch(r'/api/generations/([a-f0-9-]{36})/rename', path)
        manage_match = re.fullmatch(r'/api/generations/([a-f0-9-]{36})/(favorite|trash|restore)', path)
        if self.command == 'POST' and (path == '/api/generations' or rename_match or manage_match):
            if not secrets.compare_digest(self.headers.get('X-Prelude-CSRF', ''), self.csrf):
                raise StudioError('页面会话已变化，请刷新后核对任务', 403, 'csrf')
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise StudioError('请使用 JSON 提交', 415, 'content_type')
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 16384:
                    raise StudioError('提交内容过大或为空', 413, 'body_size')
                payload = json.loads(self.rfile.read(size))
            except (ValueError, UnicodeError):
                raise StudioError('提交内容无法读取') from None
            if rename_match:
                if not isinstance(payload, dict):
                    raise StudioError('提交内容格式不正确')
                return self.json_response(studio.rename(rename_match[1], self.owner, payload.get('title')))
            if manage_match:
                return self.json_response(studio.manage(manage_match[1], self.owner, manage_match[2], payload))
            return self.json_response(studio.create(self.owner, payload), 202)
        raise StudioError('不支持的请求', 405, 'method')

    def handle_request(self):
        try:
            self.dispatch()
        except StudioError as error:
            self.json_response({'error': str(error), 'code': error.code}, error.status)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.json_response({'error': '服务暂时不可用，原任务已保留，请刷新后核对', 'code': 'server_error'}, 500)

    do_GET = do_HEAD = do_POST = handle_request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=4173)
    args = parser.parse_args()
    os.umask(0o077)
    runtime = ROOT / '.studio'
    runtime.mkdir(exist_ok=True, mode=0o700)
    process_lock = (runtime / '.server.lock').open('a')
    try:
        fcntl.flock(process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('Prelude Studio is already running. Open http://127.0.0.1:4173 instead.') from None
    try:
        provider = Bailian()
    except JobError:
        provider = None
    limit = int(os.environ.get('PRELUDE_DAILY_LIMIT_CENTS', '3000'))
    if not 300 <= limit <= 30000:
        raise SystemExit('PRELUDE_DAILY_LIMIT_CENTS must be between 300 and 30000.')
    studio = Studio(runtime, provider, daily_limit_cents=limit)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.studio = studio
    worker = threading.Thread(target=studio.run_worker, daemon=True)
    worker.start()
    print(f'Prelude Studio: http://127.0.0.1:{server.server_port}', flush=True)
    print('Bailian configured: ' + str(provider is not None), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        studio.stop.set()
        studio.wake.set()
        server.server_close()


if __name__ == '__main__':
    main()
